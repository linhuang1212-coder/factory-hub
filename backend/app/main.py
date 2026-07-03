"""FactoryHub 入口：建表 + 种子管理员 + 路由 + 静态前端。"""
import os
import secrets

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .database import Base, engine, SessionLocal
from . import models  # noqa: F401  保证建表
from .routers import inbounds, stock, transfers, customers, styles, auth_routes
from .config import settings
from .security import hash_password, require_auth

Base.metadata.create_all(bind=engine)


def _migrate_sqlite():
    """SQLite 轻量迁移：给既有表补新列（create_all 不会改旧表）。幂等：列已存在则跳过。"""
    from sqlalchemy import text
    stmts = [
        "ALTER TABLE transfer_orders ADD COLUMN customer_id INTEGER",
        "ALTER TABLE transfer_orders ADD COLUMN customer_name VARCHAR(100)",
    ]
    with engine.connect() as conn:
        for s in stmts:
            try:
                conn.execute(text(s))
                conn.commit()
            except Exception:
                pass  # 列已存在


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

app = FastAPI(title="FactoryHub 工厂端", version="0.4.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

app.include_router(auth_routes.router)
app.include_router(inbounds.router)
app.include_router(stock.router)
app.include_router(transfers.router)
app.include_router(customers.router)
app.include_router(styles.router)


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


# 静态前端挂在根路径（放最后，API 路由优先匹配）
_FRONTEND = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "frontend"
)
if os.path.isdir(_FRONTEND):
    app.mount("/", StaticFiles(directory=_FRONTEND, html=True), name="frontend")
