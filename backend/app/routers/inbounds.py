"""工厂入库：板房把自己生产的货入进工厂库存。保存即生效（一步式，贴合板房节奏）。"""
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import FactoryInbound, StockItem
from ..schemas import InboundIn
from ..doc_no import gen_inbound_no
from ..security import require_auth

router = APIRouter(prefix="/api/inbounds", tags=["inbounds"],
                   dependencies=[Depends(require_auth)])


def item_dict(it: StockItem) -> dict:
    return {
        "id": it.id, "style_no": it.style_no, "product_name": it.product_name,
        "fineness": it.fineness, "weight": it.weight, "labor_cost": it.labor_cost,
        "piece_count": it.piece_count, "piece_labor_cost": it.piece_labor_cost,
        "ring_size": it.ring_size, "gold_price": it.gold_price, "remark": it.remark,
        "status": it.status, "inbound_id": it.inbound_id, "transfer_id": it.transfer_id,
    }


def _inbound_dict(o: FactoryInbound, with_items=False) -> dict:
    total_w = sum((Decimal(it.weight or "0") for it in o.items), Decimal("0"))
    d = {
        "id": o.id, "order_no": o.order_no, "order_date": o.order_date,
        "operator": o.operator, "remark": o.remark,
        "item_count": len(o.items), "total_weight": str(total_w),
        "deletable": all(it.status == "in_stock" for it in o.items),
        "created_at": o.created_at.isoformat() if o.created_at else None,
    }
    if with_items:
        d["items"] = [item_dict(it) for it in o.items]
    return d


@router.get("")
def list_inbounds(db: Session = Depends(get_db)):
    rows = db.query(FactoryInbound).order_by(FactoryInbound.id.desc()).limit(200).all()
    return {"success": True, "data": [_inbound_dict(o) for o in rows]}


@router.get("/{oid}")
def get_inbound(oid: int, db: Session = Depends(get_db)):
    o = db.query(FactoryInbound).filter(FactoryInbound.id == oid).first()
    if not o:
        raise HTTPException(404, "入库单不存在")
    return {"success": True, "data": _inbound_dict(o, with_items=True)}


@router.post("")
def create_inbound(data: InboundIn, user: dict = Depends(require_auth), db: Session = Depends(get_db)):
    if not data.items:
        raise HTTPException(400, "入库单没有明细")
    o = FactoryInbound(
        order_no=gen_inbound_no(db),
        order_date=(data.order_date or datetime.now().strftime("%Y-%m-%d")),
        operator=user.get("username"), remark=data.remark,
    )
    db.add(o)
    db.flush()
    for it in data.items:
        db.add(StockItem(inbound_id=o.id, status="in_stock", **it.model_dump()))
    db.commit()
    db.refresh(o)
    return {"success": True, "data": _inbound_dict(o, with_items=True)}


@router.put("/{oid}")
def update_inbound(oid: int, data: InboundIn, db: Session = Depends(get_db)):
    """编辑入库单（改日期/备注/明细）。仅当整单货都还在库（未进转移）才可改——整单重置明细。"""
    o = db.query(FactoryInbound).filter(FactoryInbound.id == oid).first()
    if not o:
        raise HTTPException(404, "入库单不存在")
    if any(it.status != "in_stock" for it in o.items):
        raise HTTPException(400, "该单有货已进转移流程，不能编辑（如需清理请强制删除后重建）")
    if not data.items:
        raise HTTPException(400, "入库单没有明细")
    if data.order_date:
        o.order_date = data.order_date
    o.remark = data.remark
    for it in list(o.items):
        db.delete(it)
    db.flush()
    for it in data.items:
        db.add(StockItem(inbound_id=o.id, status="in_stock", **it.model_dump()))
    db.commit()
    db.refresh(o)
    return {"success": True, "data": _inbound_dict(o, with_items=True)}


@router.delete("/{oid}")
def delete_inbound(oid: int, force: bool = False, db: Session = Depends(get_db)):
    """删入库单=货退出工厂库存。默认仅整单在库可删；force=true 时已进转移的也可删
    （一并删其货品 + 因此清空的转移单；对方门店若已生成预入库单需在门店另行删除）。"""
    from ..models import TransferOrder
    o = db.query(FactoryInbound).filter(FactoryInbound.id == oid).first()
    if not o:
        raise HTTPException(404, "入库单不存在")
    if not force and any(it.status != "in_stock" for it in o.items):
        raise HTTPException(400, "该单有货已进转移流程，不能删除（可强制删除）")
    tids = {it.transfer_id for it in o.items if it.transfer_id}
    for it in list(o.items):
        db.delete(it)
    db.flush()
    # 删掉因此清空的转移单（若转移单里还有别的入库单的货则保留）
    for tid in tids:
        if db.query(StockItem).filter(StockItem.transfer_id == tid).count() == 0:
            t = db.query(TransferOrder).filter(TransferOrder.id == tid).first()
            if t:
                db.delete(t)
    db.delete(o)
    db.commit()
    return {"success": True, "forced": bool(force)}
