"""工厂入库：板房把自己生产的货入进工厂库存。保存即生效（一步式，贴合板房节奏）。"""
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import FactoryInbound, StockItem, TransferOrder
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
        "is_unique": it.is_unique, "product_code": it.product_code,
        "status": it.status, "inbound_id": it.inbound_id, "transfer_id": it.transfer_id,
        "inbound_order_no": (it.inbound.order_no if it.inbound else None),   # 来源收货单号(=一"包"),出货单汇总按此分组
    }


def _dec(x) -> Decimal:
    """安全转 Decimal：None/空串/脏值 → 0（守精度，不把脏值吞成异常）。"""
    s = str(x).strip() if x is not None else ""
    if not s:
        return Decimal("0")
    try:
        return Decimal(s)
    except Exception:
        return Decimal("0")


def _item_labor(it: StockItem) -> Decimal:
    """单件工费 = 过秤克重×克工费 + 件数×附加费（同 fblerp total_cost 口径）。"""
    return _dec(it.weight) * _dec(it.labor_cost) + Decimal(it.piece_count or 0) * _dec(it.piece_labor_cost)


def _load_tmap(db: Session, rows) -> dict:
    """一次性把这些收货单关联到的出货单(TransferOrder)查出来做 {id: t} 映射，
    供 _inbound_dict 判"门店已收货"，避免逐行 N+1。"""
    tids = set()
    for o in rows:
        for it in o.items:
            if it.transfer_id:
                tids.add(it.transfer_id)
    if not tids:
        return {}
    return {t.id: t for t in db.query(TransferOrder).filter(TransferOrder.id.in_(tids)).all()}


def _ship_status(item_statuses, tstatuses) -> str:
    """收货单一条时间线的态：pending 全在库待发 / partial 部分已发 / shipped 全部已发·等门店收 /
    received 门店已收货 / empty 无货。件级 in_stock=未发；关联出货单全 confirmed=门店已收。"""
    n = len(item_statuses)
    if n == 0:
        return "empty"
    n_instock = sum(1 for s in item_statuses if s == "in_stock")
    if n_instock == n:
        return "pending"
    if n_instock > 0:
        return "partial"
    if tstatuses and all(s == "confirmed" for s in tstatuses):
        return "received"
    return "shipped"


def _inbound_dict(o: FactoryInbound, with_items=False, tmap=None) -> dict:
    total_w = sum((_dec(it.weight) for it in o.items), Decimal("0"))
    total_labor = sum((_item_labor(it) for it in o.items), Decimal("0"))
    item_statuses = [it.status for it in o.items]
    tids = {it.transfer_id for it in o.items if it.transfer_id}
    ts = [tmap[t] for t in tids if tmap and t in tmap]
    store_nos = [t.store_order_no for t in ts if t.store_order_no]
    # 发往门店名：优先收货时选的 target；没选但已发货，则从关联出货单回退取门店名（老单/拆单发的都能显示真实门店）
    target_name = (o.target_customer.name if o.target_customer else None)
    if not target_name and ts:
        tnames = list(dict.fromkeys(t.customer_name for t in ts if t.customer_name))
        if tnames:
            target_name = tnames[0] if len(tnames) == 1 else "；".join(tnames)
    d = {
        "id": o.id, "order_no": o.order_no, "order_date": o.order_date,
        "operator": o.operator, "receiver": o.receiver, "remark": o.remark,
        "target_customer_id": o.target_customer_id,
        "target_customer_name": target_name,
        "ship_status": _ship_status(item_statuses, [t.status for t in ts]),
        "can_ship": any(s == "in_stock" for s in item_statuses),
        "store_order_no": (store_nos[0] if len(store_nos) == 1
                           else (";".join(store_nos) if store_nos else None)),
        "transfer_count": len(ts),
        # 这张收货单进了哪张出货单：只在"恰好一张"时给（合并发货时多张收货单共用同一 transfer_id）→
        # 前端据此把"合并进同一张出货单"的多张收货单收拢成一行。多张/无 = None，不收拢。
        "ship_transfer_id": (ts[0].id if len(ts) == 1 else None),
        "ship_transfer_no": (ts[0].transfer_no if len(ts) == 1 else None),
        "item_count": len(o.items), "total_weight": str(total_w),
        "total_labor": str(total_labor),
        "deletable": all(s == "in_stock" for s in item_statuses),
        "created_at": o.created_at.isoformat() if o.created_at else None,
    }
    # 全局搜索文本：一个 blob 汇总所有可搜字段，前端 _q 对它做子串匹配（用户不知道要按哪种号搜，就都能搜到）：
    # 收货单号 / 备注 / 发往门店 / 门店单号(RK…) / 出货单号(ZY…) + 每行 款号(如 ZY20260518直营订单)/编码(如 FBL20260627-20)/品名
    _parts = [o.order_no or "", o.remark or "", target_name or ""]
    _parts += [s for s in store_nos if s]
    _parts += [t.transfer_no for t in ts if getattr(t, "transfer_no", None)]
    for it in o.items:
        _parts += [it.style_no or "", it.product_code or "", it.product_name or ""]
    d["search_text"] = " ".join(p for p in _parts if p)
    if with_items:
        d["items"] = [item_dict(it) for it in o.items]
    return d


@router.get("")
def list_inbounds(db: Session = Depends(get_db)):
    rows = db.query(FactoryInbound).filter(FactoryInbound.deleted_at.is_(None)).order_by(FactoryInbound.id.desc()).limit(200).all()
    tmap = _load_tmap(db, rows)
    return {"success": True, "data": [_inbound_dict(o, tmap=tmap) for o in rows]}


@router.get("/product-names")
def product_names(db: Session = Depends(get_db)):
    """品名联想历史：录过的品名(stock_items 去重去空、不含软删)，最近用过的在前。
    ★必须定义在 /{oid} 之前，否则会被当成 oid=product-names 命中而 422。"""
    rows = (db.query(StockItem.product_name)
            .filter(StockItem.product_name.isnot(None), StockItem.product_name != "",
                    StockItem.deleted_at.is_(None))
            .order_by(StockItem.id.desc()).limit(3000).all())
    seen, out = set(), []
    for (n,) in rows:
        n = (n or "").strip()
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return {"success": True, "data": out}


@router.get("/{oid}")
def get_inbound(oid: int, db: Session = Depends(get_db)):
    o = db.query(FactoryInbound).filter(FactoryInbound.id == oid, FactoryInbound.deleted_at.is_(None)).first()
    if not o:
        raise HTTPException(404, "入库单不存在")
    return {"success": True, "data": _inbound_dict(o, with_items=True, tmap=_load_tmap(db, [o]))}


@router.post("")
def create_inbound(data: InboundIn, user: dict = Depends(require_auth), db: Session = Depends(get_db)):
    if not data.items:
        raise HTTPException(400, "入库单没有明细")
    o = FactoryInbound(
        order_no=gen_inbound_no(db),
        order_date=(data.order_date or datetime.now().strftime("%Y-%m-%d")),
        operator=user.get("username"), receiver=data.receiver, remark=data.remark,
        target_customer_id=data.target_customer_id,   # 收货时选好发货门店 → 收货记录可一键发货
    )
    db.add(o)
    db.flush()
    for it in data.items:
        db.add(StockItem(inbound_id=o.id, status="in_stock", **it.model_dump(exclude={"id"})))
    db.commit()
    db.refresh(o)
    return {"success": True, "data": _inbound_dict(o, with_items=True)}


@router.put("/{oid}")
def update_inbound(oid: int, data: InboundIn, db: Session = Depends(get_db)):
    """编辑入库单。三种情形:
    ①整单在库(未发货)→整单重置明细,可加/删行。
    ②已发货但门店未收货→按 id【就地改现有行内容】(不删重建、保住出货单挂接);改后需到出货单点【重推】同步门店;不能加/删行。
    ③门店已确认收货→锁死不可改。"""
    o = db.query(FactoryInbound).filter(FactoryInbound.id == oid, FactoryInbound.deleted_at.is_(None)).first()
    if not o:
        raise HTTPException(404, "入库单不存在")
    if not data.items:
        raise HTTPException(400, "入库单没有明细")

    if all(it.status == "in_stock" for it in o.items):
        # ① 全在库：整单重置明细（可增删行）
        if data.order_date:
            o.order_date = data.order_date
        o.receiver = data.receiver
        o.target_customer_id = data.target_customer_id
        o.remark = data.remark
        for it in list(o.items):
            db.delete(it)
        db.flush()
        for it in data.items:
            db.add(StockItem(inbound_id=o.id, status="in_stock", **it.model_dump(exclude={"id"})))
        db.commit()
        db.refresh(o)
        return {"success": True, "data": _inbound_dict(o, with_items=True, tmap=_load_tmap(db, [o]))}

    # ③ 门店已确认收货 → 锁死
    tmap = _load_tmap(db, [o])
    tids = {it.transfer_id for it in o.items if it.transfer_id}
    if any((t in tmap and tmap[t].status == "confirmed") for t in tids):
        raise HTTPException(400, "该单的货门店已确认收货、已进门店库存，不能再改（如需更正请两边一起对账）")

    # ② 已发货未收货 → 就地改现有行内容（按 id 一一匹配，不许加/删行）
    by_id = {it.id: it for it in o.items}
    payload = [pi for pi in data.items if pi.id is not None]
    if len(payload) != len(o.items) or {pi.id for pi in payload} != set(by_id.keys()):
        raise HTTPException(400, "该单已发货，只能修改现有明细的内容，不能新增/删除行（如需增删请先撤回发货：到出货单反确认+删除后再改）")
    for pi in payload:
        it = by_id[pi.id]
        it.style_no = pi.style_no
        it.product_name = pi.product_name
        it.fineness = pi.fineness
        it.weight = pi.weight
        it.labor_cost = pi.labor_cost
        it.piece_count = pi.piece_count
        it.piece_labor_cost = pi.piece_labor_cost
        it.ring_size = pi.ring_size
        it.gold_price = pi.gold_price
        it.remark = pi.remark
        # is_unique/status/transfer_id/product_code 不动（涉发码与出货单挂接）
    if data.order_date:
        o.order_date = data.order_date
    o.receiver = data.receiver
    o.remark = data.remark
    # 已发货不在此改门店（改门店走出货单「改门店」）
    db.commit()
    db.refresh(o)
    return {"success": True, "shipped_edit": True,
            "data": _inbound_dict(o, with_items=True, tmap=_load_tmap(db, [o]))}


@router.delete("/{oid}")
def delete_inbound(oid: int, force: bool = False, db: Session = Depends(get_db)):
    """删收货单→进回收站(软删,保留30天可恢复/可彻底删)。仅整单在库可删；
    货已进出货流程请先处理对应出货单。"""
    o = db.query(FactoryInbound).filter(FactoryInbound.id == oid,
                                        FactoryInbound.deleted_at.is_(None)).first()
    if not o:
        raise HTTPException(404, "入库单不存在")
    if any(it.status != "in_stock" for it in o.items):
        raise HTTPException(400, "该单有货已进出货流程，请先处理对应出货单，再删本收货单")
    now = datetime.now()
    o.deleted_at = now
    for it in o.items:
        it.deleted_at = now      # 冻结货:从在手货隐藏(回收站可恢复)
    db.commit()
    return {"success": True, "recycled": True}
