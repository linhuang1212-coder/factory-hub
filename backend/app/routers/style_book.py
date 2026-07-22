# -*- coding: utf-8 -*-
"""工厂自有电子板房：款号资料库 CRUD + 图片上传 + 以图搜款(ONNX 指纹 + Python 余弦)。
克重/费率仅参考默认值，绝不参与任何库存/对账/克重计算(铁律)。"""
import json
import pathlib
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import FactoryStyle
from ..security import require_auth
from ..services import image_embed

router = APIRouter(prefix="/api/style-book", tags=["stylebook"],
                   dependencies=[Depends(require_auth)])

_UPLOAD_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "uploads" / "styles"
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
_ALLOWED = {"image/jpeg", "image/png", "image/webp"}


def _dict(s: FactoryStyle) -> dict:
    return {
        "id": s.id, "style_no": s.style_no, "name": s.name, "category": s.category,
        "fineness": s.fineness, "ref_weight": s.ref_weight, "labor_rate": s.labor_rate,
        "extra_fee": s.extra_fee, "remark": s.remark, "main_image": s.main_image,
        "status": s.status, "has_image_search": bool(s.image_embedding),
        "source": getattr(s, "source", None),   # store=门店板房同步来的(门店为准,同步会覆盖)
    }


class StyleIn(BaseModel):
    style_no: str
    name: Optional[str] = None
    category: Optional[str] = None
    fineness: Optional[str] = None
    ref_weight: Optional[str] = None
    labor_rate: Optional[str] = None
    extra_fee: Optional[str] = None
    remark: Optional[str] = None
    main_image: Optional[str] = None


def _embed(s: FactoryStyle):
    """有主图就算指纹入库(以图搜款用)；无图/失败则清空。绝不因指纹失败阻断建款。"""
    if s.main_image:
        try:
            s.image_embedding = json.dumps(image_embed.embed_main_image(s.main_image))
            s.embedding_model = image_embed.MODEL_VERSION
            return
        except Exception:
            pass
    s.image_embedding = None
    s.embedding_model = None


@router.get("")
def list_styles(q: str = Query(""), category: str = Query(""), db: Session = Depends(get_db)):
    query = db.query(FactoryStyle).filter(FactoryStyle.status == "active")   # 停产/门店已删的款不进列表
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(or_(FactoryStyle.style_no.ilike(like),
                                 FactoryStyle.name.ilike(like),
                                 FactoryStyle.fineness.ilike(like)))
    if category.strip():
        query = query.filter(FactoryStyle.category == category.strip())
    rows = query.order_by(FactoryStyle.style_no).limit(1000).all()
    return {"success": True, "data": [_dict(r) for r in rows], "model_ready": image_embed.is_available()}


@router.get("/{sid}")
def get_style(sid: int, db: Session = Depends(get_db)):
    s = db.query(FactoryStyle).filter(FactoryStyle.id == sid).first()
    if not s:
        raise HTTPException(404, "款号不存在")
    return {"success": True, "data": _dict(s)}


@router.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    """上传款号图片 → 落 backend/uploads/styles/ → 返回相对URL(/uploads/styles/xxx)。"""
    if file.content_type not in _ALLOWED:
        raise HTTPException(400, "只支持 jpg/png/webp 图片")
    content = await file.read()
    if len(content) > 12 * 1024 * 1024:
        raise HTTPException(400, "图片不能超过 12MB")
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[file.content_type]
    fname = uuid.uuid4().hex + ext
    (_UPLOAD_DIR / fname).write_bytes(content)
    return {"success": True, "url": f"/uploads/styles/{fname}"}


@router.post("")
def create_style(data: StyleIn, db: Session = Depends(get_db)):
    sn = (data.style_no or "").strip()
    if not sn:
        raise HTTPException(400, "款号必填")
    if db.query(FactoryStyle).filter(FactoryStyle.style_no == sn).first():
        raise HTTPException(409, f"款号 {sn} 已存在")
    s = FactoryStyle(style_no=sn, name=data.name, category=data.category, fineness=data.fineness,
                     ref_weight=data.ref_weight, labor_rate=data.labor_rate, extra_fee=data.extra_fee,
                     remark=data.remark, main_image=data.main_image, status="active")
    _embed(s)
    db.add(s)
    db.commit()
    db.refresh(s)
    return {"success": True, "data": _dict(s)}


@router.put("/{sid}")
def update_style(sid: int, data: StyleIn, db: Session = Depends(get_db)):
    s = db.query(FactoryStyle).filter(FactoryStyle.id == sid).first()
    if not s:
        raise HTTPException(404, "款号不存在")
    sn = (data.style_no or "").strip()
    if sn and sn != s.style_no:
        if db.query(FactoryStyle).filter(FactoryStyle.style_no == sn, FactoryStyle.id != sid).first():
            raise HTTPException(409, f"款号 {sn} 已存在")
        s.style_no = sn
    img_changed = (data.main_image or None) != (s.main_image or None)
    s.name, s.category, s.fineness = data.name, data.category, data.fineness
    s.ref_weight, s.labor_rate, s.extra_fee = data.ref_weight, data.labor_rate, data.extra_fee
    s.remark, s.main_image = data.remark, data.main_image
    if img_changed:
        _embed(s)
    db.commit()
    db.refresh(s)
    return {"success": True, "data": _dict(s)}


@router.delete("/{sid}")
def delete_style(sid: int, db: Session = Depends(get_db)):
    s = db.query(FactoryStyle).filter(FactoryStyle.id == sid).first()
    if not s:
        raise HTTPException(404, "款号不存在")
    db.delete(s)
    db.commit()
    return {"success": True}


_BOOK_SYNC_LOCK = __import__("threading").Lock()


def _name_overlap(a, b) -> bool:
    """本厂供应商名与门店款默认供应商名对得上：精确或互相包含('梵贝琳'↔'梵贝琳工厂')。"""
    a = (a or "").strip()
    b = (b or "").strip()
    return bool(a) and bool(b) and (a == b or a in b or b in a)


@router.post("/sync-from-store")
def sync_from_store(db: Session = Depends(get_db)):
    """从门店电子板房同步【本厂生产的款】(门店按默认供应商过滤)进工厂板房。
    规则：门店为准——同款号覆盖并标 source=store(含与工厂自建款撞号的情况)；
    但门店【没填】的参考克重/工费不清工厂手填值；门店停产/删掉的款,工厂侧自动转 inactive；
    款式图下载到本地并重算以图搜款指纹,只在门店换图时重新下载(store_image_url 变更检测)。"""
    if not _BOOK_SYNC_LOCK.acquire(blocking=False):
        raise HTTPException(409, "款式同步正在进行中，请稍候(首次带图同步需几分钟)")
    try:
        return _sync_from_store_impl(db)
    finally:
        _BOOK_SYNC_LOCK.release()


def _sync_from_store_impl(db: Session):
    from datetime import datetime as _dt
    from ..models import Customer
    from ..services import store_client
    customers = db.query(Customer).filter(Customer.enabled == 1).all()
    report, created, updated, img_ok, img_fail = [], 0, 0, 0, 0
    seen = set()
    all_ok = bool(customers)
    for c in customers:
        try:
            res = store_client.fetch_store_styles(c, mine=True)
            if not res.get("ok"):
                all_ok = False
                report.append({"customer": c.name, "ok": False, "reason": res.get("reason")})
                continue
            rows = res.get("data") or []
            # 旧版门店防线：响应行没有 default_supplier 键=门店端未升级,mine 过滤没生效——
            # 拒绝同步,否则会把门店全量板房(含别家供应商的款)灌进工厂并覆盖自建款
            if rows and "default_supplier" not in rows[0]:
                all_ok = False
                report.append({"customer": c.name, "ok": False,
                               "reason": "门店系统未升级,暂不支持板房同步"})
                continue
            n_c = 0
            for it in rows:
                sn = (it.get("style_no") or "").strip()
                if not sn or sn in seen:      # 多门店同款号：先到先得
                    continue
                # 双保险：工厂侧再核一遍默认供应商确实是本厂
                if not _name_overlap(c.supplier_name, it.get("default_supplier")):
                    continue
                seen.add(sn)
                s = db.query(FactoryStyle).filter(FactoryStyle.style_no == sn).first()
                if not s:
                    s = FactoryStyle(style_no=sn, status="active")
                    db.add(s)
                    created += 1
                else:
                    updated += 1
                if it.get("name"):
                    s.name = it.get("name")
                s.category = it.get("category") or s.category
                s.fineness = it.get("fineness") or s.fineness
                # 门店填了才覆盖——门店空值不清工厂手填的参考克重/工费(防静默丢数)
                if it.get("estimated_weight"):
                    s.ref_weight = it.get("estimated_weight")
                if it.get("cost_labor_rate"):
                    s.labor_rate = it.get("cost_labor_rate")
                if it.get("cost_extra_fee"):
                    s.extra_fee = it.get("cost_extra_fee")
                s.status = it.get("status") or "active"   # 门店停产(discontinued)照实镜像
                s.source = "store"
                s.synced_at = _dt.now()
                remote_img = it.get("main_image")
                if remote_img and remote_img != (getattr(s, "store_image_url", None) or ""):
                    got = store_client.download_image(c, remote_img)
                    if got.get("ok"):
                        fname = "store_" + uuid.uuid4().hex + got["ext"]
                        (_UPLOAD_DIR / fname).write_bytes(got["content"])
                        s.main_image = f"/uploads/styles/{fname}"
                        s.store_image_url = remote_img
                        _embed(s)
                        img_ok += 1
                    else:
                        img_fail += 1
                elif not remote_img and getattr(s, "store_image_url", None):
                    # 门店把图清掉了 → 工厂侧同步清(指纹一并清)
                    s.main_image = None
                    s.store_image_url = None
                    _embed(s)
                db.commit()   # 逐款提交：nginx超时/中断也保留已完成进度,下次继续
                n_c += 1
            report.append({"customer": c.name, "ok": True, "styles": n_c})
        except Exception as e:
            all_ok = False
            try:
                db.rollback()
            except Exception:
                pass
            report.append({"customer": c.name, "ok": False, "reason": f"error: {e}"})
    # 缺席停用：所有门店都拉全了才判——同步来的款(source=store)这次没出现=门店已删/收编改号 → 转 inactive
    deactivated = 0
    if all_ok:
        for s in db.query(FactoryStyle).filter(FactoryStyle.source == "store",
                                               FactoryStyle.status == "active").all():
            if s.style_no not in seen:
                s.status = "inactive"
                deactivated += 1
        db.commit()
    return {"success": True, "data": {"created": created, "updated": updated,
                                      "image_downloaded": img_ok, "image_failed": img_fail,
                                      "deactivated": deactivated, "report": report}}


@router.post("/search-by-image")
async def search_by_image(file: UploadFile = File(...), top_n: int = Form(12),
                          db: Session = Depends(get_db)):
    """以图搜款：上传照片 → 算指纹 → 与库内款号指纹算余弦 → topN(只读召回)。"""
    if not image_embed.is_available():
        raise HTTPException(400, "以图搜款模型缺失（backend/models/clip_vision_int8.onnx）")
    content = await file.read()
    try:
        qv = image_embed.embed_image_bytes(content)
    except Exception as e:
        raise HTTPException(400, f"图片识别失败: {e}")
    import numpy as np
    q = np.array(qv, dtype=np.float32)
    rows = db.query(FactoryStyle).filter(FactoryStyle.image_embedding.isnot(None),
                                         FactoryStyle.status == "active").all()
    scored = []
    for s in rows:
        try:
            v = np.array(json.loads(s.image_embedding), dtype=np.float32)
            scored.append((float(np.dot(q, v)), s))   # 均 L2 归一化 → 点积=余弦
        except Exception:
            continue
    scored.sort(key=lambda x: x[0], reverse=True)
    lim = max(1, min(int(top_n), 30))
    data = [{**_dict(s), "similarity": round(sim, 4)} for sim, s in scored[:lim]]
    return {"success": True, "data": data, "candidates": len(scored)}
