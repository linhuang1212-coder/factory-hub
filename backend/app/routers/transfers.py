"""转移商品部：从工厂库存挑货 → 转移单(ZY) → 推送门店成预入库 draft。
件状态流转：in_stock → reserved(进转移草稿即锁定，防重复转移) → transferred(推送成功)。"""
import json
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import TransferOrder, StockItem, Customer
from ..schemas import TransferCreateIn
from ..doc_no import gen_transfer_no
from ..security import require_auth
from ..services import store_client
from .inbounds import item_dict

router = APIRouter(prefix="/api/transfers", tags=["transfers"],
                   dependencies=[Depends(require_auth)])

STATUS_LABEL = {"draft": "待转移", "pushed": "已转移", "confirmed": "门店已收货"}


def _transfer_dict(t: TransferOrder, with_items=False) -> dict:
    total_w = sum((Decimal(it.weight or "0") for it in t.items), Decimal("0"))
    d = {
        "id": t.id, "transfer_no": t.transfer_no,
        "customer_id": t.customer_id, "customer_name": t.customer_name,
        "status": t.status,
        "status_label": STATUS_LABEL.get(t.status, t.status),
        "locked": bool(t.locked),
        "store_order_no": t.store_order_no, "operator": t.operator, "remark": t.remark,
        "item_count": len(t.items), "total_weight": str(total_w),
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "pushed_at": t.pushed_at.isoformat() if t.pushed_at else None,
    }
    if with_items:
        d["items"] = [item_dict(it) for it in t.items]
        d["push_response"] = t.push_response
    return d


def _get_or_404(db: Session, tid: int) -> TransferOrder:
    t = db.query(TransferOrder).filter(TransferOrder.id == tid).first()
    if not t:
        raise HTTPException(404, "转移单不存在")
    return t


@router.get("")
def list_transfers(db: Session = Depends(get_db)):
    rows = db.query(TransferOrder).order_by(TransferOrder.id.desc()).limit(200).all()
    return {"success": True, "data": [_transfer_dict(t) for t in rows]}


@router.get("/{tid}")
def get_transfer(tid: int, db: Session = Depends(get_db)):
    return {"success": True, "data": _transfer_dict(_get_or_404(db, tid), with_items=True)}


@router.post("")
def create_transfer(data: TransferCreateIn, user: dict = Depends(require_auth),
                    db: Session = Depends(get_db)):
    if not data.item_ids:
        raise HTTPException(400, "请先勾选要转移的货品")
    cust = db.query(Customer).filter(Customer.id == data.customer_id).first()
    if not cust:
        raise HTTPException(400, "请选择转移给哪个客户")
    if not cust.enabled:
        raise HTTPException(400, f"客户「{cust.name}」已停用")
    items = db.query(StockItem).filter(StockItem.id.in_(data.item_ids)).all()
    if len(items) != len(set(data.item_ids)):
        raise HTTPException(400, "部分货品不存在，请刷新后重试")
    not_free = [it for it in items if it.status != "in_stock"]
    if not_free:
        names = "、".join(f"{it.product_name}(#{it.id})" for it in not_free[:5])
        raise HTTPException(400, f"以下货品不在库（已锁定或已转移）：{names}")
    t = TransferOrder(transfer_no=gen_transfer_no(db), status="draft",
                      customer_id=cust.id, customer_name=cust.name,
                      operator=user.get("username"), remark=data.remark)
    db.add(t)
    db.flush()
    for it in items:
        it.transfer_id = t.id
        it.status = "reserved"
    db.commit()
    db.refresh(t)
    return {"success": True, "data": _transfer_dict(t, with_items=True)}


@router.post("/{tid}/confirm")
def confirm_transfer(tid: int, db: Session = Depends(get_db)):
    """确认（锁定）转移单：锁定后不可删除，须先反确认。转移成功已自动锁，此接口用于反确认后再锁回。"""
    t = _get_or_404(db, tid)
    t.locked = 1
    db.commit()
    db.refresh(t)
    return {"success": True, "data": _transfer_dict(t)}


@router.post("/{tid}/unconfirm")
def unconfirm_transfer(tid: int, db: Session = Depends(get_db)):
    """反确认（解锁）转移单：解锁后方可删除。不改变货品状态、不回退门店预入库单。"""
    t = _get_or_404(db, tid)
    t.locked = 0
    db.commit()
    db.refresh(t)
    return {"success": True, "data": _transfer_dict(t)}


@router.delete("/{tid}")
def delete_transfer(tid: int, force: bool = False, db: Session = Depends(get_db)):
    """draft 可删（货解锁回在库）；force=true 时已推送/已确认的也可删（货同样解锁回在库；
    对方门店若已生成预入库单，需在门店另行删除，两边系统各自独立）。
    ★已「确认锁定」的单一律不可删（含 force），须先反确认——防误删已转移单。"""
    t = _get_or_404(db, tid)
    if t.locked:
        raise HTTPException(400, "该转移单已确认锁定，请先「反确认」再删除")
    if t.status != "draft" and not force:
        raise HTTPException(400, f"转移单状态为「{STATUS_LABEL.get(t.status, t.status)}」，不能删除（可强制删除）")
    for it in list(t.items):
        it.status = "in_stock"
        it.transfer_id = None
    db.delete(t)
    db.commit()
    return {"success": True, "forced": bool(force)}


@router.post("/{tid}/push")
def push_transfer(tid: int, db: Session = Depends(get_db)):
    """推送门店成预入库 draft。失败留痕、货保持锁定，可安全重推（ZY 单号幂等）。"""
    t = _get_or_404(db, tid)
    if not t.items:
        raise HTTPException(400, "转移单没有货品")
    if t.status == "confirmed":
        raise HTTPException(400, "客户已确认收货，不能重复转移")
    cust = db.query(Customer).filter(Customer.id == t.customer_id).first() if t.customer_id else None
    if not cust:
        raise HTTPException(400, "该转移单未关联客户（旧数据），请删除后重新创建")

    result = store_client.push_pre_inbound(t, t.items, cust)
    t.push_response = json.dumps(result, ensure_ascii=False)
    if result.get("ok"):
        t.status = "pushed"
        t.locked = 1                       # 转移成功即自动锁定，防误删（须反确认才可删）
        t.store_order_no = result.get("store_order_no")
        t.pushed_at = datetime.now()
        for it in t.items:
            it.status = "transferred"
        db.commit()
        db.refresh(t)
        return {"success": True, "data": _transfer_dict(t, with_items=True), "push": result}

    db.commit()  # 失败也留痕 push_response，货保持 reserved
    db.refresh(t)
    return {"success": False, "message": result.get("message"),
            "push": result, "data": _transfer_dict(t, with_items=True)}
