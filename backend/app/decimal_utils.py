"""精度工具。珠宝铁律：克重/金额全程 Decimal，绝不经过 float。
本工厂端把克重/金额存为字符串(TEXT)，规避 SQLite NUMERIC 退化成 float 的坑。"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

WEIGHT_Q = Decimal("0.0001")  # 克重 4 位
MONEY_Q = Decimal("0.01")     # 金额 2 位


def parse_decimal(v):
    """把任意输入安全转 Decimal。None/空 -> None。非法 -> ValueError。
    注意：先 str() 再 Decimal，float 字面量进来也按其字符串表示解析（调用方应传字符串）。"""
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        raise ValueError(f"非法数值: {v!r}")


def round_weight(d: Decimal) -> Decimal:
    return d.quantize(WEIGHT_Q, rounding=ROUND_HALF_UP)


def round_money(d: Decimal) -> Decimal:
    return d.quantize(MONEY_Q, rounding=ROUND_HALF_UP)


def normalize(v, q: Decimal):
    """转 Decimal 并按精度量化，回标准字符串入库。None -> None。"""
    d = parse_decimal(v)
    if d is None:
        return None
    return str(d.quantize(q, rounding=ROUND_HALF_UP))
