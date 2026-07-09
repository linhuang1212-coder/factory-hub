"""转移商品部：从工厂库存挑货 → 转移单(ZY) → 推送门店成预入库 draft。
件状态流转：in_stock → reserved(进转移草稿即锁定，防重复转移) → transferred(推送成功)。"""
import json
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import TransferOrder, StockItem, Customer, FactoryInbound
from ..schemas import TransferCreateIn
from ..doc_no import gen_transfer_no
from ..security import require_auth
from ..services import store_client
from .inbounds import item_dict

router = APIRouter(prefix="/api/transfers", tags=["transfers"],
                   dependencies=[Depends(require_auth)])

STATUS_LABEL = {"draft": "待发货", "pushed": "已发货", "confirmed": "门店已收货"}


def _assign_factory_codes(db, prefix, items):
    """给一码一件的在库件按门店前缀发码（prefix+8位）。前缀 max+1，工厂独占该前缀→不撞码。
    已有码的件不重发；称重件(is_unique=0)不发码。返回本次发码数。"""
    if not prefix:
        return 0
    last = (db.query(StockItem.product_code)
            .filter(StockItem.product_code.like(prefix + "%"))
            .order_by(StockItem.product_code.desc()).first())
    base = 0
    if last and last[0]:
        try:
            base = int(last[0][len(prefix):])
        except (ValueError, IndexError):
            base = 0
    n = 0
    for it in items:
        if it.is_unique and not it.product_code:
            base += 1
            it.product_code = f"{prefix}{base:08d}"
            n += 1
    return n


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
        # 收货单位（收货录入手填，如"煜桐直播"）：出货单打印优先印它，比客户档案名更贴柜台叫法
        recvs = []
        for it in t.items:
            r = ((it.inbound.receiver if it.inbound else None) or "").strip()
            if r and r not in recvs:
                recvs.append(r)
        d["receivers"] = recvs
    return d


def _get_or_404(db: Session, tid: int) -> TransferOrder:
    t = db.query(TransferOrder).filter(TransferOrder.id == tid, TransferOrder.deleted_at.is_(None)).first()
    if not t:
        raise HTTPException(404, "转移单不存在")
    return t


@router.get("")
def list_transfers(db: Session = Depends(get_db)):
    rows = db.query(TransferOrder).filter(TransferOrder.deleted_at.is_(None)).order_by(TransferOrder.id.desc()).limit(200).all()
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
    items = db.query(StockItem).filter(StockItem.id.in_(data.item_ids), StockItem.deleted_at.is_(None)).all()
    if len(items) != len(set(data.item_ids)):
        raise HTTPException(400, "部分货品不存在，请刷新后重试")
    not_free = [it for it in items if it.status != "in_stock"]
    if not_free:
        names = "、".join(f"{it.product_name}(#{it.id})" for it in not_free[:5])
        raise HTTPException(400, f"以下货品不在库（已锁定或已转移）：{names}")
    # 合并成【一张】出货单：挑中的多包货（可跨多张收货单，但都发往同一门店）合成一张 → 门店只生成一张
    # 预入库单、过秤入库一次（2026-07-07 用户拍板：原来按收货单拆成多张，10 包要门店入 10 遍太费事）。
    # 单据/打印/导出里仍逐件列明细（汇总单 + 明细表），追溯靠每件的款号/编码。
    t = TransferOrder(transfer_no=gen_transfer_no(db), status="draft",
                      customer_id=cust.id, customer_name=cust.name,
                      operator=user.get("username"), remark=data.remark)
    db.add(t)
    db.flush()
    for it in items:
        it.transfer_id = t.id
        it.status = "reserved"
    _assign_factory_codes(db, cust.code_prefix, items)   # 方案B：按门店前缀给一码一件发码
    db.commit()
    db.refresh(t)
    return {"success": True, "data": {
        "count": 1,
        "transfers": [_transfer_dict(t, with_items=True)],
    }}


@router.post("/from-inbound/{inbound_id}")
def ship_inbound(inbound_id: int, body: dict = Body(default=None),
                 user: dict = Depends(require_auth), db: Session = Depends(get_db)):
    """一键发货（收完即发）：整张收货单在库的货 → 一张出货单 → 直接推送门店。
    门店优先取请求体 customer_id，否则用收货单登记的 target_customer_id。
    推送没成功则整体回滚（收货单保持"待发货"、货不动），修好门店 Key 后可重发。"""
    body = body or {}
    inb = db.query(FactoryInbound).filter(
        FactoryInbound.id == inbound_id, FactoryInbound.deleted_at.is_(None)).first()
    if not inb:
        raise HTTPException(404, "收货单不存在")
    items = [it for it in inb.items if it.status == "in_stock" and it.deleted_at is None]
    if not items:
        raise HTTPException(400, "这张收货单没有在库可发的货（可能已发货或已删除）")
    cid = body.get("customer_id") or inb.target_customer_id
    if not cid:
        raise HTTPException(400, "这张收货单还没指定发货门店，请选择要发给哪个门店")
    cust = db.query(Customer).filter(Customer.id == cid).first()
    if not cust:
        raise HTTPException(400, "发货门店无效")
    if not cust.enabled:
        raise HTTPException(400, f"门店「{cust.name}」已停用")
    # 整张收货单 = 一张出货单（不拆）：建单 → 锁货 → 按门店前缀发码 → 推送
    t = TransferOrder(transfer_no=gen_transfer_no(db), status="draft",
                      customer_id=cust.id, customer_name=cust.name,
                      operator=user.get("username"),
                      remark=(body.get("remark") or f"整单发货 · {inb.order_no}"))
    db.add(t)
    db.flush()
    for it in items:
        it.transfer_id = t.id
        it.status = "reserved"
    _assign_factory_codes(db, cust.code_prefix, items)
    result = store_client.push_pre_inbound(t, items, cust)
    if not result.get("ok"):
        db.rollback()   # 推送没成功 → 撤销建单+锁货，收货单回"待发货"可重发
        return {"success": False, "message": result.get("message"), "push": result}
    t.push_response = json.dumps(result, ensure_ascii=False)
    t.status = "pushed"
    t.locked = 1
    t.store_order_no = result.get("store_order_no")
    t.pushed_at = datetime.now()
    for it in items:
        it.status = "transferred"
    if not inb.target_customer_id:   # 当时没记门店的，顺手回填
        inb.target_customer_id = cust.id
    db.commit()
    db.refresh(t)
    return {"success": True, "data": _transfer_dict(t, with_items=True), "push": result}


@router.post("/from-inbounds")
def ship_inbounds(body: dict = Body(...), user: dict = Depends(require_auth),
                  db: Session = Depends(get_db)):
    """多张收货单【合并发货】：勾选的几张收货单里所有在库货 → 合成【一张】出货单 → 一次推门店
    （门店只生成一张预入库单、只过秤入库一次；单据/打印/导出仍逐件列明细）。
    所有单须发往同一门店：请求体带 customer_id 用它，否则取各收货单登记的 target（须一致）。
    推送没成功则整体回滚（收货单保持"待发货"、货不动），修好门店 Key 后可重发。"""
    ids = [int(x) for x in (body.get("inbound_ids") or [])]
    if not ids:
        raise HTTPException(400, "请先勾选要合并发货的收货单")
    inbs = db.query(FactoryInbound).filter(
        FactoryInbound.id.in_(ids), FactoryInbound.deleted_at.is_(None)).all()
    if len(inbs) != len(set(ids)):
        raise HTTPException(400, "部分收货单不存在，请刷新后重试")
    items = [it for inb in inbs for it in inb.items
             if it.status == "in_stock" and it.deleted_at is None]
    if not items:
        raise HTTPException(400, "所选收货单都没有在库可发的货（可能已发货或已删除）")
    cid = body.get("customer_id")
    if not cid:   # 没显式指定门店 → 从收货单登记的门店推断（必须一致，否则不给合并）
        targets = {inb.target_customer_id for inb in inbs if inb.target_customer_id}
        if len(targets) == 1:
            cid = next(iter(targets))
        elif not targets:
            raise HTTPException(400, "所选收货单都还没指定发货门店，请先选要发给哪个门店")
        else:
            raise HTTPException(400, "所选收货单发往的门店不一致，不能合并成一张；请只选发往同一门店的，或统一指定一个门店")
    cust = db.query(Customer).filter(Customer.id == cid).first()
    if not cust:
        raise HTTPException(400, "发货门店无效")
    if not cust.enabled:
        raise HTTPException(400, f"门店「{cust.name}」已停用")
    # 建【一张】出货单（合并多单）→ 锁货 → 按门店前缀发码 → 一次推送
    order_nos = "、".join(inb.order_no for inb in inbs)
    t = TransferOrder(transfer_no=gen_transfer_no(db), status="draft",
                      customer_id=cust.id, customer_name=cust.name,
                      operator=user.get("username"),
                      remark=(body.get("remark") or f"合并发货 · {len(inbs)}单 · {order_nos}"))
    db.add(t)
    db.flush()
    for it in items:
        it.transfer_id = t.id
        it.status = "reserved"
    _assign_factory_codes(db, cust.code_prefix, items)
    result = store_client.push_pre_inbound(t, items, cust)
    if not result.get("ok"):
        db.rollback()   # 推送没成功 → 撤销建单+锁货，收货单回"待发货"可重发
        return {"success": False, "message": result.get("message"), "push": result}
    t.push_response = json.dumps(result, ensure_ascii=False)
    t.status = "pushed"
    t.locked = 1
    t.store_order_no = result.get("store_order_no")
    t.pushed_at = datetime.now()
    for it in items:
        it.status = "transferred"
    for inb in inbs:
        if not inb.target_customer_id:
            inb.target_customer_id = cust.id
    db.commit()
    db.refresh(t)
    return {"success": True, "data": _transfer_dict(t, with_items=True),
            "merged_count": len(inbs), "push": result}


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
    """反确认（解锁）转移单：解锁后方可删除/改门店。不改变货品状态、不回退门店预入库单。"""
    t = _get_or_404(db, tid)
    t.locked = 0
    db.commit()
    db.refresh(t)
    return {"success": True, "data": _transfer_dict(t)}


@router.put("/{tid}/customer")
def change_transfer_customer(tid: int, body: dict = Body(...), db: Session = Depends(get_db)):
    """改出货门店（反确认解锁后可用）：改客户 + 按新门店前缀重发一码一件码 + 退回草稿待重推。
    注意：旧门店若已生成预入库单，工厂端接口无法替其删除，需门店/人工另删。"""
    t = _get_or_404(db, tid)
    if t.locked:
        raise HTTPException(400, "请先「反确认」再改门店")
    if t.status == "confirmed":
        raise HTTPException(400, "门店已确认收货，不能改门店")
    cust = db.query(Customer).filter(Customer.id == body.get("customer_id")).first()
    if not cust or not cust.enabled:
        raise HTTPException(400, "目标门店无效或已停用")
    old_name = t.customer_name
    changed = cust.id != t.customer_id
    t.customer_id = cust.id
    t.customer_name = cust.name
    items = db.query(StockItem).filter(StockItem.transfer_id == t.id).all()
    if changed:
        for it in items:
            if getattr(it, "is_unique", False):
                it.product_code = None            # 换门店 → 清旧码
        _assign_factory_codes(db, cust.code_prefix, items)   # 按新门店前缀重发一码一件码
    for it in items:
        it.status = "reserved"
    t.status = "draft"
    t.store_order_no = None
    t.pushed_at = None
    db.commit()
    db.refresh(t)
    note = f"已改到「{cust.name}」，请点「转移」推送。"
    if changed and old_name and old_name != cust.name:
        note += f" ⚠ 旧门店「{old_name}」若已生成预入库单，需去旧门店手动删（工厂端无法替门店删）。"
    return {"success": True, "data": _transfer_dict(t, with_items=True), "note": note}


@router.delete("/{tid}")
def delete_transfer(tid: int, force: bool = False, db: Session = Depends(get_db)):
    """删出货单：货【立即退回在手·清码】+ 单进回收站(30天可恢复,存记录)。已「确认锁定」须先反确认。
    （门店那边若已生成预入库单，需门店/人工另删——工厂端接口不能替门店删单。）"""
    t = _get_or_404(db, tid)
    if t.locked:
        raise HTTPException(400, "该转移单已确认锁定，请先「反确认」再删除")
    for it in db.query(StockItem).filter(StockItem.transfer_id == t.id).all():
        it.status = "in_stock"
        it.transfer_id = None
        if getattr(it, "is_unique", False):
            it.product_code = None
    t.deleted_at = datetime.now()
    db.commit()
    return {"success": True, "recycled": True}


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


@router.post("/refresh-status")
def refresh_status(db: Session = Depends(get_db)):
    """回执闭环：逐张「已出货(pushed)」的出货单去门店查预入库单状态，门店已过秤入账(confirmed)
    则本地翻成"门店已收货"。门店把单删了(found=False)记 store_missing。返回本次核对/更新数。"""
    rows = db.query(TransferOrder).filter(TransferOrder.status == "pushed", TransferOrder.deleted_at.is_(None)).all()
    cust_cache = {}
    checked = updated = missing = errors = 0
    for t in rows:
        cid = t.customer_id
        cust = cust_cache.get(cid)
        if cust is None and cid:
            cust = db.query(Customer).filter(Customer.id == cid).first()
            cust_cache[cid] = cust
        if not cust:
            continue
        checked += 1
        res = store_client.fetch_inbound_status(cust, t.transfer_no)
        if not res.get("ok"):
            errors += 1
            continue
        if res.get("found") is False:
            missing += 1
            continue
        if res.get("status") == "confirmed":
            t.status = "confirmed"
            updated += 1
    if updated:
        db.commit()
    return {"success": True, "checked": checked, "updated": updated,
            "store_missing": missing, "errors": errors}
