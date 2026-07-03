"""单据号生成：FRK-YYYYMMDD-NNN 工厂入库、ZY-YYYYMMDD-NNN 转移（按日序号）。
ZY 单号即推送门店的幂等键 factory_order_no。"""
from datetime import datetime


def _gen(db, model, field, prefix: str) -> str:
    today = datetime.now().strftime("%Y%m%d")
    full_prefix = f"{prefix}-{today}-"
    col = getattr(model, field)
    last = (
        db.query(col)
        .filter(col.like(full_prefix + "%"))
        .order_by(col.desc())
        .first()
    )
    seq = 1
    if last and last[0]:
        try:
            seq = int(last[0].rsplit("-", 1)[1]) + 1
        except (ValueError, IndexError):
            seq = 1
    return f"{full_prefix}{seq:03d}"


def gen_inbound_no(db) -> str:
    from .models import FactoryInbound
    return _gen(db, FactoryInbound, "order_no", "FRK")


def gen_transfer_no(db) -> str:
    from .models import TransferOrder
    return _gen(db, TransferOrder, "transfer_no", "ZY")
