"""登录 / 当前用户 / 账号管理（admin）。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import User
from ..security import hash_password, verify_password, make_token, require_auth, require_admin

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class UserCreateIn(BaseModel):
    username: str
    password: str
    display_name: str | None = None
    is_admin: bool = False


def _me_payload(payload: dict) -> dict:
    return {
        "username": payload.get("username"),
        "is_admin": bool(payload.get("admin")),
        "supplier_name": settings.FACTORY_SUPPLIER_NAME,
        "store_base_url": settings.STORE_BASE_URL,
        "store_key_configured": bool(settings.STORE_API_KEY),
        "style_sync_enabled": settings.STYLE_SYNC_ENABLED,
    }


@router.post("/login")
def login(data: LoginIn, db: Session = Depends(get_db)):
    u = db.query(User).filter(User.username == data.username.strip()).first()
    if not u or not verify_password(data.password, u.password_hash):
        raise HTTPException(401, "账号或密码错误")
    token = make_token(u)
    return {"success": True, "data": {
        "token": token, "username": u.username,
        "display_name": u.display_name, "is_admin": bool(u.is_admin),
    }}


@router.get("/me")
def me(payload: dict = Depends(require_auth)):
    return {"success": True, "data": _me_payload(payload)}


@router.get("/users")
def list_users(_: dict = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(User).order_by(User.id).all()
    return {"success": True, "data": [
        {"id": u.id, "username": u.username, "display_name": u.display_name,
         "is_admin": bool(u.is_admin)} for u in rows
    ]}


@router.post("/users")
def create_user(data: UserCreateIn, _: dict = Depends(require_admin), db: Session = Depends(get_db)):
    name = data.username.strip()
    if not name or not data.password or len(data.password) < 6:
        raise HTTPException(400, "账号必填，密码至少 6 位")
    if db.query(User).filter(User.username == name).first():
        raise HTTPException(409, f"账号 {name} 已存在")
    u = User(username=name, password_hash=hash_password(data.password),
             display_name=(data.display_name or "").strip() or None,
             is_admin=1 if data.is_admin else 0)
    db.add(u)
    db.commit()
    return {"success": True, "data": {"id": u.id, "username": u.username}}
