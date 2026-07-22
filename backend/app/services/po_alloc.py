# -*- coding: utf-8 -*-
"""发货自动挂订货单(二期核销)：推送门店前,按款号把本次要发的货对到该门店「未到齐」的
订货明细行(本地镜像 factory_order_items),把门店明细id 放到货品的临时属性 _po_detail_id——
build_payload 随 pre-inbound 报文带给门店,门店过秤确认那一刻自动核销(门店核销机制现成)。

规则(保守,宁缺勿错):
- 只对 store_status∈(ordered,partial) 的订单、remaining_pieces ≥ 本件件数 的行分配
  (一件货只能挂一行,装不下就换行,都装不下宁可不挂——绝不自动超收);
- 先按【归一化精确】款号全场找一遍,找不到再走前缀匹配:货品款号以订单款号开头且
  下一字符不是 ASCII 字母数字(工厂习惯'FBL..-8镶石5粒'✓;'FBL..-88'/'FBL..-8A'✗防同系列误配),
  且前缀至少5个字符(防订单行 factory_no 填'FB'之类短串吸走所有货);
- 订单按单号(DH+日期+序号,即真实下单时间)从旧到新分配;
- 同事务扣减镜像行 remaining_pieces(推送失败回滚/显式 revert;下次同步门店会校正)。
铁律:此处只传"挂钩id",克重金额账仍全部由门店入库单产生。"""


def _norm(s) -> str:
    return (str(s or "")).replace(" ", "").upper()


def _prefix_hit(stock_style: str, order_style: str) -> bool:
    a = _norm(stock_style)
    b = _norm(order_style)
    if not a or not b or len(b) < 5:
        return False
    if not a.startswith(b) or a == b:
        return False
    nxt = a[len(b):len(b) + 1]
    return not (nxt.isascii() and nxt.isalnum())   # '镶'✓ '8'✗ 'A'✗


def _line_key(pair):
    ln, order_no = pair
    return (order_no or "", ln.id)


def allocate_po_links(db, customer, items):
    """给 items(StockItem 列表)标 _po_detail_id。返回分配清单 [(item, line, pieces), ...] 供失败回退。"""
    from ..models import FactoryOrder, FactoryOrderItem
    rows = (db.query(FactoryOrderItem, FactoryOrder.order_no)
              .join(FactoryOrder, FactoryOrder.id == FactoryOrderItem.order_id)
              .filter(FactoryOrder.customer_id == customer.id,
                      FactoryOrder.store_status.in_(["ordered", "partial"]),
                      FactoryOrderItem.store_detail_id.isnot(None))
              .all())
    lines = [ln for ln, _no in sorted(rows, key=_line_key)]   # 按门店单号=真实下单顺序,从旧到新
    allocs = []

    def _try(it, pieces, exact_only):
        a = _norm(it.style_no)
        if not a:
            return None
        for ln in lines:
            rem = int(ln.remaining_pieces or 0)
            if rem < pieces:      # 装不下不挂这行(不拆件、不超收),继续找能容纳的行
                continue
            if exact_only:
                hit = (a == _norm(ln.style_no)) or (a == _norm(ln.factory_no))
            else:
                hit = _prefix_hit(it.style_no, ln.style_no) or _prefix_hit(it.style_no, ln.factory_no)
            if hit:
                it._po_detail_id = ln.store_detail_id
                ln.remaining_pieces = rem - pieces
                return (it, ln, pieces)
        return None

    for it in items:
        if getattr(it, "_po_detail_id", None):
            continue
        pieces = int(it.piece_count or 1)
        got = _try(it, pieces, True) or _try(it, pieces, False)   # 精确优先,再前缀
        if got:
            allocs.append(got)
    return allocs


def revert_po_links(allocs):
    """推送失败但仍要 commit 留痕时,把分配的扣减原额退回(分配时保证 rem>=pieces,扣退对称)。"""
    for it, ln, pieces in allocs:
        try:
            ln.remaining_pieces = int(ln.remaining_pieces or 0) + pieces
            if hasattr(it, "_po_detail_id"):
                delattr(it, "_po_detail_id")
        except Exception:
            pass
