"""款式缓存：从门店电子板房同步 + 本地查询（发货下拉用）。"""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import StyleCache
from ..security import require_auth
from ..services import store_client

router = APIRouter(prefix="/api/styles", tags=["styles"],
                    dependencies=[Depends(require_auth)])


def _row_dict(r: StyleCache) -> dict:
    return {
        "style_no": r.style_no, "name": r.name, "category": r.category,
        "fineness": r.fineness, "estimated_weight": r.estimated_weight,
        "cost_labor_rate": r.cost_labor_rate, "main_image": r.main_image, "status": r.status,
    }


@router.get("")
def list_styles(q: str = Query(""), db: Session = Depends(get_db)):
    query = db.query(StyleCache)
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(or_(StyleCache.style_no.ilike(like), StyleCache.name.ilike(like)))
    rows = query.order_by(StyleCache.style_no).limit(500).all()
    return {"success": True, "data": [_row_dict(r) for r in rows]}


@router.post("/sync")
def sync_styles(db: Session = Depends(get_db)):
    """从门店拉款式清单入缓存。门店端未就绪时返回 success=false（不报错）。"""
    if not settings.STYLE_SYNC_ENABLED:
        return {"success": False, "message": "本实例未开放款式同步（外部合作工厂请手填款号/品名）"}
    try:
        data = store_client.fetch_styles()
    except Exception as e:
        return {"success": False, "message": f"同步失败（门店端可能尚未就绪）：{e}"}

    def _s(v):
        return None if v is None else str(v)

    n = 0
    for item in data:
        sn = (item.get("style_no") or "").strip()
        if not sn:
            continue
        row = db.query(StyleCache).filter(StyleCache.style_no == sn).first()
        if not row:
            row = StyleCache(style_no=sn)
            db.add(row)
        row.name = item.get("name")
        row.category = item.get("category")
        row.fineness = item.get("fineness")
        row.estimated_weight = _s(item.get("estimated_weight"))
        row.cost_labor_rate = _s(item.get("cost_labor_rate"))
        row.main_image = item.get("main_image")
        row.status = item.get("status")
        row.synced_at = datetime.now()
        n += 1
    db.commit()
    return {"success": True, "synced": n}
