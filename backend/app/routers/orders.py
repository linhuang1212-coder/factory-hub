# -*- coding: utf-8 -*-
"""工厂「订单」模块：门店订货单(DH)只读镜像 + 工厂生产状态。
同步=按客户档案逐店拉 /api/external/purchase-orders，(customer_id, order_no) 幂等 upsert，
明细整组重建(镜像以门店为准)；工厂只维护自己的 prod_status(新单/已接单/生产中/已备货)。
铁律：件数/约重是订货意向数，不参与任何库存/对账/克重计算。"""
import threading
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Customer, FactoryOrder, FactoryOrderItem
from ..security import require_auth
from ..services import store_client

router = APIRouter(prefix="/api/orders", tags=["orders"],
                   dependencies=[Depends(require_auth)])

PROD_STATUSES = ("new", "accepted", "in_production", "ready")
_SYNC_LOCK = threading.Lock()   # 防重复点同步(nginx 504后用户重点)并发跑两遍撞唯一键


def _item_dict(it: FactoryOrderItem) -> dict:
    return {
        "id": it.id, "store_detail_id": it.store_detail_id,
        "style_no": it.style_no, "factory_no": it.factory_no,
        "product_name": it.product_name, "product_category": it.product_category,
        "sub_category": it.sub_category, "weight_range": it.weight_range,
        "fineness": it.fineness, "craft": it.craft, "spec_remark": it.spec_remark,
        "ordered_pieces": it.ordered_pieces, "ordered_weight": it.ordered_weight,
        "expect_labor_cost": it.expect_labor_cost, "labor_mode": it.labor_mode,
        "received_pieces": it.received_pieces, "received_weight": it.received_weight,
        "remaining_pieces": it.remaining_pieces, "row_status": it.row_status,
        "remark": it.remark, "image_url": it.image_url,
    }


def _order_dict(o: FactoryOrder, with_items: bool = True) -> dict:
    d = {
        "id": o.id, "customer_id": o.customer_id, "customer_name": o.customer_name,
        "order_no": o.order_no, "store_status": o.store_status,
        "order_date": o.order_date, "expected_date": o.expected_date,
        "supplier_name": o.supplier_name,
        "total_pieces": o.total_pieces, "total_weight": o.total_weight,
        "received_pieces": o.received_pieces, "received_weight": o.received_weight,
        "operator": o.operator, "remark": o.remark,
        "prod_status": o.prod_status, "prod_note": o.prod_note,
        "synced_at": (o.synced_at.strftime("%Y-%m-%d %H:%M") if o.synced_at else None),
    }
    if with_items:
        d["items"] = [_item_dict(it) for it in
                      sorted(o.items, key=lambda x: (x.store_detail_id or 0, x.id))]
    return d


@router.get("")
def list_orders(store_status: str = Query(""), prod: str = Query(""),
                customer_id: int = Query(0), q: str = Query(""),
                db: Session = Depends(get_db)):
    """订单列表(本地镜像)。store_status: open=未到齐(ordered/partial) / 具体状态 / 空=全部。"""
    query = db.query(FactoryOrder)
    st = store_status.strip()
    if st == "open":
        query = query.filter(FactoryOrder.store_status.in_(["ordered", "partial"]))
    elif st:
        query = query.filter(FactoryOrder.store_status == st)
    if prod.strip():
        query = query.filter(FactoryOrder.prod_status == prod.strip())
    if customer_id:
        query = query.filter(FactoryOrder.customer_id == customer_id)
    if q.strip():
        like = f"%{q.strip()}%"
        ids = [r[0] for r in (db.query(FactoryOrderItem.order_id)
                                .filter(or_(FactoryOrderItem.style_no.ilike(like),
                                            FactoryOrderItem.factory_no.ilike(like),
                                            FactoryOrderItem.product_name.ilike(like)))
                                .distinct().all())]
        query = query.filter(or_(FactoryOrder.order_no.ilike(like),
                                 FactoryOrder.id.in_(ids or [0])))
    rows = query.order_by(FactoryOrder.id.desc()).limit(300).all()
    return {"success": True, "data": [_order_dict(o) for o in rows]}


@router.post("/sync")
def sync_orders(db: Session = Depends(get_db)):
    """从所有启用客户拉订货单。逐店独立：一家失败(网络/脏数据)不影响另一家。"""
    if not _SYNC_LOCK.acquire(blocking=False):
        raise HTTPException(409, "订单同步正在进行中，请稍候")
    try:
        return _sync_orders_impl(db)
    finally:
        _SYNC_LOCK.release()


def _sync_orders_impl(db: Session):
    customers = db.query(Customer).filter(Customer.enabled == 1).all()
    report, total_new, total_upd = [], 0, 0
    for c in customers:
        try:
            r_c = _sync_one_customer(db, c)
        except Exception as e:            # 单客户脏数据/IO错只记报告,不炸整个同步
            try:
                db.rollback()
            except Exception:
                pass
            report.append({"customer": c.name, "ok": False, "reason": f"error: {e}"})
            continue
        report.append(r_c)
        total_new += r_c.get("created", 0)
        total_upd += r_c.get("updated", 0)
    return {"success": True,
            "data": {"created": total_new, "updated": total_upd, "report": report}}


def _sync_one_customer(db: Session, c: Customer) -> dict:
    res = store_client.fetch_purchase_orders(c, status="all")
    if not res.get("ok"):
        return {"customer": c.name, "ok": False, "reason": res.get("reason")}
    created = updated = 0
    base = (c.store_base_url or "").rstrip("/")
    feed = res.get("data") or []
    feed_nos = set()
    if True:
        for od in feed:
            order_no = (od.get("order_no") or "").strip()
            if not order_no:
                continue
            feed_nos.add(order_no)
            row = (db.query(FactoryOrder)
                     .filter(FactoryOrder.customer_id == c.id,
                             FactoryOrder.order_no == order_no).first())
            if not row:
                row = FactoryOrder(customer_id=c.id, customer_name=c.name,
                                   order_no=order_no, prod_status="new")
                db.add(row)
                db.flush()
                created += 1
            else:
                updated += 1
            row.store_status = od.get("status")
            row.order_date = od.get("order_date")
            row.expected_date = od.get("expected_date")
            row.supplier_name = od.get("supplier_name")
            row.total_pieces = od.get("total_pieces")
            row.total_weight = od.get("total_weight")
            row.received_pieces = od.get("received_pieces")
            row.received_weight = od.get("received_weight")
            row.operator = od.get("operator")
            row.remark = od.get("remark")
            row.synced_at = datetime.now()
            # 明细整组重建(镜像以门店为准，工厂不改明细)
            db.query(FactoryOrderItem).filter(FactoryOrderItem.order_id == row.id).delete()
            for d in (od.get("details") or []):
                img = d.get("main_image")
                db.add(FactoryOrderItem(
                    order_id=row.id, store_detail_id=d.get("detail_id"),
                    style_no=d.get("style_no"), factory_no=d.get("factory_no"),
                    product_name=d.get("product_name"),
                    product_category=d.get("product_category"),
                    sub_category=d.get("sub_category"), weight_range=d.get("weight_range"),
                    fineness=d.get("fineness"), craft=d.get("craft"),
                    spec_remark=d.get("spec_remark"),
                    ordered_pieces=d.get("ordered_pieces"),
                    ordered_weight=d.get("ordered_weight"),
                    expect_labor_cost=d.get("expect_labor_cost"),
                    labor_mode=d.get("labor_mode"),
                    received_pieces=d.get("received_pieces"),
                    received_weight=d.get("received_weight"),
                    remaining_pieces=d.get("remaining_pieces"),
                    row_status=d.get("row_status"), remark=d.get("remark"),
                    image_url=((base + img) if (img and not str(img).startswith("http")) else img),
                ))
    # 缺席单标记：feed 未截断(条数<上限)=门店完整清单——镜像里还挂"在途"但门店已查不到的单
    # (被反确认回草稿/被删/被撤)标成 withdrawn,防止僵尸单永远挂在"未到齐"里误导排产
    withdrawn = 0
    if len(feed) < store_client.PO_FETCH_LIMIT:
        stale = (db.query(FactoryOrder)
                   .filter(FactoryOrder.customer_id == c.id,
                           FactoryOrder.store_status.in_(["ordered", "partial"]))
                   .all())
        for srow in stale:
            if srow.order_no not in feed_nos:
                srow.store_status = "withdrawn"
                srow.synced_at = datetime.now()
                withdrawn += 1
    db.commit()
    return {"customer": c.name, "ok": True, "created": created, "updated": updated,
            "withdrawn": withdrawn}


@router.delete("/{oid}")
def delete_order(oid: int, db: Session = Depends(get_db)):
    """删除本地镜像单(测试单/历史单清理)。只允许删已完结/已取消/已撤单——
    在途单(ordered/partial)删了下次同步还会回来,须先在门店取消。"""
    row = db.query(FactoryOrder).filter(FactoryOrder.id == oid).first()
    if not row:
        raise HTTPException(404, "订单不存在")
    if row.store_status in ("ordered", "partial"):
        raise HTTPException(400, "这张单在门店还是在途状态——请先在门店把订货单取消，同步后变成「已撤单」即可删除")
    db.query(FactoryOrderItem).filter(FactoryOrderItem.order_id == row.id).delete()
    db.delete(row)
    db.commit()
    return {"success": True}


class ProdStatusIn(BaseModel):
    status: str
    note: Optional[str] = None


@router.post("/{oid}/prod-status")
def set_prod_status(oid: int, data: ProdStatusIn, db: Session = Depends(get_db)):
    """工厂标记生产状态：new新单 / accepted已接单 / in_production生产中 / ready已备货。"""
    if data.status not in PROD_STATUSES:
        raise HTTPException(400, "无效状态")
    row = db.query(FactoryOrder).filter(FactoryOrder.id == oid).first()
    if not row:
        raise HTTPException(404, "订单不存在")
    row.prod_status = data.status
    if data.note is not None:
        row.prod_note = (data.note or "").strip() or None
    db.commit()
    return {"success": True, "data": {"id": row.id, "prod_status": row.prod_status}}
