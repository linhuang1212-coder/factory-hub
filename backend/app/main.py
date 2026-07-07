"""FactoryHub 入口：建表 + 种子管理员 + 路由 + 静态前端。"""
import os
import secrets

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .database import Base, engine, SessionLocal
from . import models  # noqa: F401  保证建表
from .routers import inbounds, stock, transfers, customers, styles, auth_routes, style_book, recycle
from .config import settings
from .security import hash_password, require_auth

Base.metadata.create_all(bind=engine)


def _migrate_sqlite():
    """SQLite 轻量迁移：给既有表补新列（create_all 不会改旧表）。幂等：列已存在则跳过。"""
    from sqlalchemy import text
    stmts = [
        "ALTER TABLE transfer_orders ADD COLUMN customer_id INTEGER",
        "ALTER TABLE transfer_orders ADD COLUMN customer_name VARCHAR(100)",
        "ALTER TABLE stock_items ADD COLUMN is_unique INTEGER DEFAULT 1",
        "ALTER TABLE stock_items ADD COLUMN product_code VARCHAR(20)",
        "ALTER TABLE customers ADD COLUMN code_prefix VARCHAR(4)",
        "ALTER TABLE factory_inbounds ADD COLUMN deleted_at DATETIME",
        "ALTER TABLE stock_items ADD COLUMN deleted_at DATETIME",
        "ALTER TABLE transfer_orders ADD COLUMN deleted_at DATETIME",
        "ALTER TABLE factory_inbounds ADD COLUMN receiver VARCHAR(120)",
        "ALTER TABLE factory_inbounds ADD COLUMN target_customer_id INTEGER",
    ]
    with engine.connect() as conn:
        for s in stmts:
            try:
                conn.execute(text(s))
                conn.commit()
            except Exception:
                pass  # 列已存在
        # locked 列：新建时补列 + 一次性把既有「已转移/门店已收货」单锁上；
        # 用 PRAGMA 判存在，只在"首次新建列"时回填，避免每次重启把用户反确认过的单又锁回去。
        try:
            cols = [r[1] for r in conn.execute(text("PRAGMA table_info(transfer_orders)")).fetchall()]
            if "locked" not in cols:
                conn.execute(text("ALTER TABLE transfer_orders ADD COLUMN locked INTEGER DEFAULT 0"))
                conn.execute(text("UPDATE transfer_orders SET locked=1 WHERE status IN ('pushed','confirmed')"))
                conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass


def _seed_customer():
    """首启种子默认客户：customers 表为空时，用 env 的主门店配置建第一个客户档案。"""
    db = SessionLocal()
    try:
        if db.query(models.Customer).first():
            return
        db.add(models.Customer(
            name=settings.STORE_CUSTOMER_NAME,
            store_base_url=settings.STORE_BASE_URL.rstrip("/"),
            store_api_key=settings.STORE_API_KEY or None,
            supplier_name=settings.FACTORY_SUPPLIER_NAME,
            enabled=1, remark="首启自动创建（来自 .env 主门店配置）",
        ))
        db.commit()
        print(f"[FactoryHub] 已创建默认客户档案：{settings.STORE_CUSTOMER_NAME}")
    finally:
        db.close()


def _seed_admin():
    """首启种子管理员：users 表为空时创建。密码取 env，否则随机生成并写 ADMIN_PASSWORD.txt。"""
    db = SessionLocal()
    try:
        if db.query(models.User).first():
            return
        pw = settings.ADMIN_PASSWORD
        generated = False
        if not pw:
            pw = secrets.token_urlsafe(9)
            generated = True
        db.add(models.User(username=settings.ADMIN_USER, password_hash=hash_password(pw),
                           display_name="管理员", is_admin=1))
        db.commit()
        if generated:
            path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ADMIN_PASSWORD.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"FactoryHub 初始管理员账号: {settings.ADMIN_USER}\n初始密码: {pw}\n"
                        f"（登录后请尽快改配置；本文件确认后请删除）\n")
            print(f"[FactoryHub] 已生成初始管理员 {settings.ADMIN_USER}，密码见 backend/ADMIN_PASSWORD.txt")
    finally:
        db.close()


_migrate_sqlite()
_seed_admin()
_seed_customer()
recycle.purge_expired(SessionLocal)   # 回收站:启动清一次超期软删单

app = FastAPI(title="FactoryHub 工厂端", version="0.4.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# 首页 index.html 不缓存：否则浏览器缓存旧 index → 一直加载旧 app.js（改了看不到新版）。
# 静态资源(app.js?v=/styles.css?v=)带版本号照常缓存,只对 HTML 文档禁缓存。
@app.middleware("http")
async def _no_cache_html(request, call_next):
    resp = await call_next(request)
    p = request.url.path
    if p == "/" or p.endswith("/") or p.endswith(".html"):
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp

app.include_router(auth_routes.router)
app.include_router(inbounds.router)
app.include_router(stock.router)
app.include_router(transfers.router)
app.include_router(customers.router)
app.include_router(styles.router)
app.include_router(style_book.router)
app.include_router(recycle.router)


@app.get("/api/health")
def health():
    """健康检查（开放，watchdog 用）。不含敏感信息。"""
    return {"success": True, "service": "factory-hub",
            "supplier": settings.FACTORY_SUPPLIER_NAME}


@app.get("/api/config")
def get_config(_: dict = Depends(require_auth)):
    return {"success": True, "data": {
        "supplier_name": settings.FACTORY_SUPPLIER_NAME,
        "store_base_url": settings.STORE_BASE_URL,
        "store_key_configured": bool(settings.STORE_API_KEY),
        "auto_confirm": settings.AUTO_CONFIRM,
        "style_sync_enabled": settings.STYLE_SYNC_ENABLED,
    }}


# 上传图片(款号主图)静态托管：放在前端根挂载之前，/uploads/* 由此服务
_UPLOADS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
os.makedirs(_UPLOADS, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=_UPLOADS), name="uploads")

# 静态前端挂在根路径（放最后，API 路由优先匹配）
_FRONTEND = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "frontend"
)
if os.path.isdir(_FRONTEND):
    app.mount("/", StaticFiles(directory=_FRONTEND, html=True), name="frontend")
