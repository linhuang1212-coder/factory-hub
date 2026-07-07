"""Pydantic v2 schemas。克重/金额入参用字符串，validator 转 Decimal 规范化后回存字符串。"""
from typing import List, Optional

from pydantic import BaseModel, field_validator

from .decimal_utils import normalize, parse_decimal, WEIGHT_Q, MONEY_Q


class StockItemIn(BaseModel):
    """入库明细一行（一件/一批货，板房逐件过秤）。"""
    id: Optional[int] = None                   # 编辑已发货单时按 id 就地更新(不删重建,保住出货单挂接)
    style_no: Optional[str] = None
    product_name: str
    fineness: str
    weight: str                                # 过秤克重（必填，>0）
    labor_cost: str = "0"                      # 克工费
    piece_count: Optional[int] = 1
    piece_labor_cost: Optional[str] = None     # 件工费
    ring_size: Optional[str] = None
    gold_price: Optional[str] = None           # 仅留存
    remark: Optional[str] = None
    is_unique: Optional[int] = 1               # 1=一码一件(发货发码)/0=称重(不发码)

    @field_validator("is_unique")
    @classmethod
    def _v_uniq(cls, v):
        return 0 if v in (0, "0", False, "false", "False") else 1

    @field_validator("product_name", "fineness")
    @classmethod
    def _required_text(cls, v):
        if not v or not str(v).strip():
            raise ValueError("必填")
        return str(v).strip()

    @field_validator("weight")
    @classmethod
    def _v_weight(cls, v):
        nv = normalize(v, WEIGHT_Q)
        if nv is None or parse_decimal(nv) <= 0:
            raise ValueError("过秤克重必须大于 0")
        return nv

    @field_validator("labor_cost")
    @classmethod
    def _v_labor(cls, v):
        nv = normalize(v, MONEY_Q)
        if nv is None:
            return "0.00"
        if parse_decimal(nv) < 0:
            raise ValueError("克工费不能为负")
        return nv

    @field_validator("piece_labor_cost", "gold_price")
    @classmethod
    def _v_opt_money(cls, v):
        if v is None or str(v).strip() == "":
            return None
        nv = normalize(v, MONEY_Q)
        if parse_decimal(nv) < 0:
            raise ValueError("金额不能为负")
        return nv

    @field_validator("style_no", "ring_size", "remark")
    @classmethod
    def _strip_opt(cls, v):
        if v is None:
            return None
        v = str(v).strip()
        return v or None


class InboundIn(BaseModel):
    """工厂入库单：保存即进库存。"""
    order_date: Optional[str] = None           # YYYY-MM-DD，不填默认今天
    receiver: str                              # 收货单位（必填，打印出库单左上角）
    target_customer_id: Optional[int] = None   # 发货门店（收货时可先选，一步式"收完即发"；空=暂不定）
    remark: Optional[str] = None
    items: List[StockItemIn]

    @field_validator("receiver")
    @classmethod
    def _v_receiver(cls, v):
        if not v or not str(v).strip():
            raise ValueError("收货单位必填")
        return str(v).strip()


class TransferCreateIn(BaseModel):
    """新建转移单：从在库件里挑 + 指定转移给哪个客户。"""
    customer_id: int
    item_ids: List[int]
    remark: Optional[str] = None


class CustomerIn(BaseModel):
    """新建客户档案。"""
    name: str
    store_base_url: str
    supplier_name: str
    store_api_key: Optional[str] = None
    code_prefix: Optional[str] = None          # 工厂发码前缀 TF/FF；空=该门店不发码
    remark: Optional[str] = None

    @field_validator("name", "store_base_url", "supplier_name")
    @classmethod
    def _req(cls, v):
        if not v or not str(v).strip():
            raise ValueError("必填")
        return str(v).strip()

    @field_validator("code_prefix")
    @classmethod
    def _v_prefix(cls, v):
        if v is None:
            return None
        v = str(v).strip().upper()
        return v or None


class CustomerUpdate(BaseModel):
    """编辑客户：只更新给到的字段；store_api_key 传非空才覆盖（永不回显旧值）。
    code_prefix 传空串=清除该门店前缀、传 None=不改（router 里区分处理）。"""
    name: Optional[str] = None
    store_base_url: Optional[str] = None
    supplier_name: Optional[str] = None
    store_api_key: Optional[str] = None
    enabled: Optional[bool] = None
    code_prefix: Optional[str] = None
    remark: Optional[str] = None
