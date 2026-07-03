"""ORM 模型。业务同构 AI-ERP：产品入库(FRK)→工厂库存→转移商品部(ZY=门店预入库)。
克重/金额字段一律 String（守精度，见 decimal_utils）。"""
from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from .database import Base


class FactoryInbound(Base):
    """工厂入库单（FRK 前缀）：板房把自己生产的货入进工厂库存。保存即生效。"""
    __tablename__ = "factory_inbounds"

    id = Column(Integer, primary_key=True, index=True)
    order_no = Column(String(64), unique=True, index=True, nullable=False)  # FRK-YYYYMMDD-NNN
    order_date = Column(String(10), nullable=False)       # YYYY-MM-DD
    operator = Column(String(50), nullable=True)          # 录入人(登录账号)
    remark = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)

    items = relationship("StockItem", back_populates="inbound")


class StockItem(Base):
    """库存件：一行=一件/一批货。入库产生，转移消耗。
    status: in_stock 在库 / reserved 已锁定(在转移草稿里) / transferred 已转移门店"""
    __tablename__ = "stock_items"

    id = Column(Integer, primary_key=True, index=True)
    inbound_id = Column(Integer, ForeignKey("factory_inbounds.id"), index=True)
    transfer_id = Column(Integer, ForeignKey("transfer_orders.id"), nullable=True, index=True)
    style_no = Column(String(50), nullable=True)          # 款号（关门店电子板房）
    product_name = Column(String(200), nullable=False)
    fineness = Column(String(50), nullable=False)         # 成色（足金999/18K金/S925银…）
    weight = Column(String(32), nullable=False)           # ★过秤克重（转移后=门店 expected_weight 预报重）
    labor_cost = Column(String(32), nullable=False, default="0")  # 克工费
    piece_count = Column(Integer, default=1)
    piece_labor_cost = Column(String(32), nullable=True)  # 件工费
    ring_size = Column(String(20), nullable=True)         # 手寸
    gold_price = Column(String(32), nullable=True)        # 随货作价（仅留存）
    remark = Column(Text, nullable=True)
    status = Column(String(20), default="in_stock", index=True)
    created_at = Column(DateTime, default=datetime.now)

    inbound = relationship("FactoryInbound", back_populates="items")
    transfer = relationship("TransferOrder", back_populates="items")


class Customer(Base):
    """客户档案（=收货方门店/公司）。一客户一端点一Key，支持一厂多客户。
    store_api_key 是对方发的对接密钥，只写不读（前端永不回显）。"""
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)   # 客户名，如 梵贝琳门店
    store_base_url = Column(String(300), nullable=False)      # 对方 ERP 地址
    store_api_key = Column(String(200), nullable=True)        # 对方发的 X-API-Key
    supplier_name = Column(String(100), nullable=False)       # 本厂在对方 ERP 里的供应商名(一字不差)
    enabled = Column(Integer, default=1)                      # 1启用/0停用
    remark = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)


class TransferOrder(Base):
    """转移单（ZY 前缀）：从工厂库存挑货转移给指定客户（推送=对方预入库 draft）。"""
    __tablename__ = "transfer_orders"

    id = Column(Integer, primary_key=True, index=True)
    transfer_no = Column(String(64), unique=True, index=True, nullable=False)  # ZY-YYYYMMDD-NNN，幂等键
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    customer_name = Column(String(100), nullable=True)    # 建单时快照，防客户改名后历史单漂移
    status = Column(String(20), default="draft", index=True)  # draft / pushed / confirmed
    store_order_no = Column(String(50), nullable=True)    # 对方回执单号(如 RK…)
    operator = Column(String(50), nullable=True)
    remark = Column(Text, nullable=True)
    push_response = Column(Text, nullable=True)           # 最近一次推送原始返回（排错）
    locked = Column(Integer, default=0)                   # 1=已确认锁定(不可删)，0=未锁/已反确认；转移成功即自动锁
    created_at = Column(DateTime, default=datetime.now)
    pushed_at = Column(DateTime, nullable=True)

    customer = relationship("Customer")
    items = relationship("StockItem", back_populates="transfer")


class User(Base):
    """登录账号（每工厂实例独立一套，绝不跨库）。"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(200), nullable=False)   # pbkdf2: salt$hexhash
    display_name = Column(String(50), nullable=True)
    is_admin = Column(Integer, default=0)                  # SQLite 布尔用 int
    created_at = Column(DateTime, default=datetime.now)


class StyleCache(Base):
    """从门店电子板房拉来的款式清单缓存（入库下拉用，只读）。"""
    __tablename__ = "style_cache"

    style_no = Column(String(50), primary_key=True)
    name = Column(String(200), nullable=True)
    category = Column(String(50), nullable=True)
    fineness = Column(String(50), nullable=True)
    estimated_weight = Column(String(32), nullable=True)   # 参考克重
    cost_labor_rate = Column(String(32), nullable=True)    # 参考克工费
    main_image = Column(String(500), nullable=True)
    status = Column(String(20), nullable=True)
    synced_at = Column(DateTime, default=datetime.now)
