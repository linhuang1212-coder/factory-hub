"""账号密码 + 签名令牌。零第三方依赖：stdlib pbkdf2(密码) + HMAC-SHA256(令牌)。
每实例独立密钥（env FACTORY_AUTH_SECRET，缺省时首启生成并存 .auth_secret 文件）。"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Optional

from fastapi import Depends, Header, HTTPException

from .config import settings

_PBKDF2_ITER = 200_000
TOKEN_TTL_SECONDS = 7 * 86400  # 7 天

# ---------- 密码 ----------

def hash_password(pw: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITER)
    return f"{salt}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        salt, expected = stored.split("$", 1)
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITER)
        return hmac.compare_digest(dk.hex(), expected)
    except Exception:
        return False


# ---------- 实例密钥 ----------

_secret_cache: Optional[bytes] = None


def _secret() -> bytes:
    global _secret_cache
    if _secret_cache is not None:
        return _secret_cache
    if settings.AUTH_SECRET:
        _secret_cache = settings.AUTH_SECRET.encode("utf-8")
        return _secret_cache
    # 缺省：首启生成随机密钥并持久化，避免每次重启令牌全失效
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".auth_secret")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            _secret_cache = f.read().strip().encode("utf-8")
    else:
        val = secrets.token_urlsafe(32)
        with open(path, "w", encoding="utf-8") as f:
            f.write(val)
        _secret_cache = val.encode("utf-8")
    return _secret_cache


# ---------- 令牌 ----------

def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(user) -> str:
    payload = {
        "uid": user.id,
        "username": user.username,
        "admin": bool(user.is_admin),
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sig = hmac.new(_secret(), raw, hashlib.sha256).hexdigest()
    return f"{_b64e(raw)}.{sig}"


def parse_token(token: str) -> Optional[dict]:
    try:
        body, sig = token.split(".", 1)
        raw = _b64d(body)
        expect = hmac.new(_secret(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return None
        payload = json.loads(raw)
        if int(payload.get("exp", 0)) < time.time():
            return None
        return payload
    except Exception:
        return None


# ---------- FastAPI 依赖 ----------

def require_auth(authorization: Optional[str] = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    payload = parse_token(authorization[7:].strip())
    if not payload:
        raise HTTPException(401, "登录已过期，请重新登录")
    return payload


def require_admin(user: dict = Depends(require_auth)) -> dict:
    if not user.get("admin"):
        raise HTTPException(403, "需要管理员权限")
    return user
