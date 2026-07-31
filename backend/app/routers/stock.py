"""工厂库存：在库件查询与汇总（支持按克重区间筛）。"""
from decimal import Decimal, InvalidOperation

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


MAX_ROWS = 500


def _w(s):
    """克重文本 → Decimal；空值或非法返回 None。

    铁律：weight 存的是 TEXT（守 Decimal 精度，见 decimal_utils），比大小绝不能
    交给 SQL —— 那走的是字典序。线上真实数据里按文本排最大是 '98.8300'，而实际
    最大是 '1330.6200'；直接用 SQL 筛「≥100g」会一件都筛不出来。
    """
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _hit(v, lo, hi) -> bool:
    """lo/hi 均为闭区间，任一为 None 表示该侧不设限。"""
    if v is None:
        return False
    return not ((lo is not None and v < lo) or (hi is not None and v > hi))


def _weight_filter(rows, lo, hi, mode):
    """mode='order' 按整单总克重筛（命中则该单的行全留）；否则按单件过秤克重筛。

    整单合计只累加当前已经筛出来的行，口径和前端折叠分组里显示的「总克重」一致。
    """
    if mode == "order":
        totals = {}
        for r in rows:
            totals[r.inbound_id] = totals.get(r.inbound_id, Decimal("0")) + (_w(r.weight) or Decimal("0"))
        keep = {k for k, v in totals.items() if _hit(v, lo, hi)}
        return [r for r in rows if r.inbound_id in keep]
    return [r for r in rows if _hit(_w(r.weight), lo, hi)]


@router.get("")
def list_stock(q: str = Query(""), status: str = Query("all"),
               wmin: str = Query("", description="克重下限(含)，空=不限"),
               wmax: str = Query("", description="克重上限(含)，空=不限"),
               wmode: str = Query("item", description="item=按单件克重 / order=按整单总克重"),
               db: Session = Depends(get_db)):
    query = db.query(StockItem).filter(StockItem.status.in_(STATUS_SETS.get(status, STATUS_SETS["all"])), StockItem.deleted_at.is_(None))
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(or_(StockItem.product_name.ilike(like),
                                 StockItem.style_no.ilike(like),
                                 StockItem.fineness.ilike(like)))
    query = query.order_by(StockItem.id.desc())

    lo, hi = _w(wmin), _w(wmax)
    truncated = False
    if lo is None and hi is None:
        rows = query.limit(MAX_ROWS).all()
    else:
        # ★顺序要紧：先全量取回、Decimal 过滤完，最后才截断。
        #   反过来先 limit(MAX_ROWS) 的话，范围外的货会先把名额占满 → 筛出来的结果缺货。
        hits = _weight_filter(query.all(), lo, hi, wmode)
        truncated = len(hits) > MAX_ROWS
        rows = hits[:MAX_ROWS]

    def _sum(statuses):
        sel = [r for r in rows if r.status in statuses]
        return {"count": len(sel),
                "weight": str(sum((Decimal(r.weight or "0") for r in sel), Decimal("0")))}

    def _row(r):
        d = item_dict(r)
        d["inbound_order_no"] = r.inbound.order_no if r.inbound else None  # 供前端按入库单折叠分组
        return d

    resp = {"success": True,
            "summary": {"in_stock": _sum(("in_stock",)), "reserved": _sum(("reserved",)),
                        "transferred": _sum(("transferred",))},
            "data": [_row(r) for r in rows]}
    if lo is not None or hi is not None:
        # 回给前端：单件模式下折叠分组里只剩命中的件，那一行的「总克重」不等于整单实际
        # 重量，必须在界面上讲明白，否则会被当成整单变轻了。
        resp["weight_filter"] = {"mode": "order" if wmode == "order" else "item",
                                 "min": str(lo) if lo is not None else None,
                                 "max": str(hi) if hi is not None else None,
                                 "truncated": truncated}
    return resp
