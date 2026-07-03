"""工厂库存：在库件查询与汇总。"""
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import StockItem
from ..security import require_auth
from .inbounds import item_dict

router = APIRouter(prefix="/api/stock", tags=["stock"],
                   dependencies=[Depends(require_auth)])

STATUS_SETS = {
    "in_stock": ("in_stock",),
    "reserved": ("reserved",),
    "transferred": ("transferred",),
    "available": ("in_stock",),
    "onhand": ("in_stock", "reserved"),   # 在手货=已收货、还没出货（在库+待出货）
    "all": ("in_stock", "reserved", "transferred"),
}


@router.get("")
def list_stock(q: str = Query(""), status: str = Query("all"), db: Session = Depends(get_db)):
    query = db.query(StockItem).filter(StockItem.status.in_(STATUS_SETS.get(status, STATUS_SETS["all"])))
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(or_(StockItem.product_name.ilike(like),
                                 StockItem.style_no.ilike(like),
                                 StockItem.fineness.ilike(like)))
    rows = query.order_by(StockItem.id.desc()).limit(500).all()

    def _sum(statuses):
        sel = [r for r in rows if r.status in statuses]
        return {"count": len(sel),
                "weight": str(sum((Decimal(r.weight or "0") for r in sel), Decimal("0")))}

    def _row(r):
        d = item_dict(r)
        d["inbound_order_no"] = r.inbound.order_no if r.inbound else None  # 供前端按入库单折叠分组
        return d

    return {"success": True,
            "summary": {"in_stock": _sum(("in_stock",)), "reserved": _sum(("reserved",)),
                        "transferred": _sum(("transferred",))},
            "data": [_row(r) for r in rows]}
