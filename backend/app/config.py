"""配置：从 backend/.env 读取，零依赖 pydantic-settings。"""
import os
from dotenv import load_dotenv

# 显式指向 backend/.env，避免 cwd 不同导致读不到
_ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
load_dotenv(dotenv_path=_ENV_PATH)


def _as_bool(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


class Settings:
    STORE_BASE_URL = os.getenv("STORE_BASE_URL", "http://127.0.0.1:9000")
    STORE_API_KEY = os.getenv("STORE_API_KEY", "").strip()
    FACTORY_SUPPLIER_NAME = os.getenv("FACTORY_SUPPLIER_NAME", "梵贝琳工厂")
    STORE_CUSTOMER_NAME = os.getenv("STORE_CUSTOMER_NAME", "梵贝琳门店")  # 首启种子的默认客户名
    AUTO_CONFIRM = _as_bool(os.getenv("AUTO_CONFIRM", "false"))
    PUSHED_BY = os.getenv("PUSHED_BY", "factory-hub")
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./factory_hub.db")
    PORT = int(os.getenv("FACTORY_HUB_PORT", "8200"))
    # ---- 登录 ----
    AUTH_SECRET = os.getenv("FACTORY_AUTH_SECRET", "").strip()   # 空=首启自动生成存 .auth_secret
    ADMIN_USER = os.getenv("FACTORY_ADMIN_USER", "admin").strip()
    ADMIN_PASSWORD = os.getenv("FACTORY_ADMIN_PASSWORD", "").strip()  # 空=首启随机生成并写 ADMIN_PASSWORD.txt
    # ---- 功能开关 ----
    STYLE_SYNC_ENABLED = _as_bool(os.getenv("STYLE_SYNC_ENABLED", "true"))  # 外部合作工厂实例应设 false


settings = Settings()
