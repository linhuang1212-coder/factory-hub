# -*- coding: utf-8 -*-
"""回收站：软删除的单据(收货单FRK/出货单ZY)保留 RETENTION_DAYS 天可恢复，到期自动真删。
- 收货单软删=单+其货冻结(deleted_at)，从在手货隐藏；恢复=货回在手；真删=硬删单+货。
- 出货单软删=单 deleted_at；货保持删前状态(仍绑本单)，恢复即复原；真删/到期=货退回在手清码+硬删单。"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import FactoryInbound, TransferOrder, StockItem
from ..security import require_auth
from .inbounds import _inbound_dict
from .transfers import _transfer_dict

router = APIRouter(prefix="/api/recycle-bin", tags=["recycle"],
                   dependencies=[Depends(require_auth)])

RETENTION_DAYS = 30


def _days_left(deleted_at):
    if not deleted_at:
        return None
    left = (deleted_at + timedelta(days=RETENTION_DAYS) - datetime.now()).days
    return max(0, left)


def _do_purge(db, cutoff):
    """硬删 deleted_at < cutoff 的单：收货单删单+货；出货单退货回在手清码+删单。"""
    for o in (db.query(FactoryInbound)
              .filter(FactoryInbound.deleted_at.isnot(None), FactoryInbound.deleted_at < cutoff).all()):
        db.query(StockItem).filter(StockItem.inbound_id == o.id).delete()
        db.delete(o)
    for t in (db.query(TransferOrder)
              .filter(TransferOrder.deleted_at.isnot(None), TransferOrder.deleted_at < cutoff).all()):
        for it in db.query(StockItem).filter(StockItem.transfer_id == t.id).all():
            it.status = "in_stock"
            it.transfer_id = None
            it.product_code = None
        db.delete(t)
    db.commit()


def purge_expired(SessionLocal):
    """启动时调：清掉超期(30天)的软删单。幂等，失败不阻断启动。"""
    db = SessionLocal()
    try:
        _do_purge(db, datetime.now() - timedelta(days=RETENTION_DAYS))
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


@router.get("")
def list_recycle(db: Session = Depends(get_db)):
    _do_purge(db, datetime.now() - timedelta(days=RETENTION_DAYS))   # 进回收站先清一次过期
    data = []
    for o in (db.query(FactoryInbound).filter(FactoryInbound.deleted_at.isnot(None))
              .order_by(FactoryInbound.deleted_at.desc()).all()):
        d = _inbound_dict(o)
        data.append({"type": "inbound", "type_label": "收货单", "id": o.id, "order_no": o.order_no,
                     "deleted_at": o.deleted_at.isoformat(), "days_left": _days_left(o.deleted_at),
                     "item_count": d["item_count"], "total_weight": d["total_weight"], "customer_name": None})
    for t in (db.query(TransferOrder).filter(TransferOrder.deleted_at.isnot(None))
              .order_by(TransferOrder.deleted_at.desc()).all()):
        d = _transfer_dict(t)
        data.append({"type": "transfer", "type_label": "出货单", "id": t.id, "order_no": t.transfer_no,
                     "deleted_at": t.deleted_at.isoformat(), "days_left": _days_left(t.deleted_at),
                     "item_count": d["item_count"], "total_weight": d["total_weight"], "customer_name": t.customer_name})
    data.sort(key=lambda x: x["deleted_at"], reverse=True)
    return {"success": True, "data": data, "retention_days": RETENTION_DAYS}


@router.post("/restore/{doc_type}/{doc_id}")
def restore(doc_type: str, doc_id: int, db: Session = Depends(get_db)):
    if doc_type == "inbound":
        o = db.query(FactoryInbound).filter(FactoryInbound.id == doc_id,
                                            FactoryInbound.deleted_at.isnot(None)).first()
        if not o:
            raise HTTPException(404, "回收站里没有这张收货单")
        o.deleted_at = None
        db.query(StockItem).filter(StockItem.inbound_id == o.id).update({"deleted_at": None})
        db.commit()
        return {"success": True, "message": f"收货单 {o.order_no} 已恢复"}
    if doc_type == "transfer":
        t = db.query(TransferOrder).filter(TransferOrder.id == doc_id,
                                           TransferOrder.deleted_at.isnot(None)).first()
        if not t:
            raise HTTPException(404, "回收站里没有这张出货单")
        t.deleted_at = None
        db.commit()
        return {"success": True, "message": f"出货单 {t.transfer_no} 已恢复"}
    raise HTTPException(400, "未知单据类型")


@router.delete("/purge/{doc_type}/{doc_id}")
def purge_one(doc_type: str, doc_id: int, db: Session = Depends(get_db)):
    """立即彻底删除(不等30天)。收货单:删单+货;出货单:货退回在手清码+删单。"""
    if doc_type == "inbound":
        o = db.query(FactoryInbound).filter(FactoryInbound.id == doc_id,
                                            FactoryInbound.deleted_at.isnot(None)).first()
        if not o:
            raise HTTPException(404, "回收站里没有这张收货单")
        db.query(StockItem).filter(StockItem.inbound_id == o.id).delete()
        db.delete(o)
        db.commit()
        return {"success": True}
    if doc_type == "transfer":
        t = db.query(TransferOrder).filter(TransferOrder.id == doc_id,
                                           TransferOrder.deleted_at.isnot(None)).first()
        if not t:
            raise HTTPException(404, "回收站里没有这张出货单")
        for it in db.query(StockItem).filter(StockItem.transfer_id == t.id).all():
            it.status = "in_stock"
            it.transfer_id = None
            it.product_code = None
        db.delete(t)
        db.commit()
        return {"success": True}
    raise HTTPException(400, "未知单据类型")
