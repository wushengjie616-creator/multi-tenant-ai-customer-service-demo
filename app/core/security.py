"""JWT 与密码安全（NFR3-01 / NFR3-02）。

身份与租户**只**来自已验证 token（architecture §4）：
- 签发：`create_access_token(tenant_id, user_id, role)`
- 校验：`decode_token(token)` -> `AuthContext`，失败抛 `AuthError`
- 密码：PBKDF2-SHA256（标准库实现，避免新增依赖），存储格式
  `pbkdf2_sha256$<iterations>$<salt_hex>$<digest_hex>`
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from app.core.config import settings

ROLE_USER = "user"
ROLE_AGENT = "agent"
ROLE_ADMIN = "admin"
ROLES = (ROLE_USER, ROLE_AGENT, ROLE_ADMIN)

_PBKDF2_ITERATIONS = 260_000


class AuthError(Exception):
    """认证/授权失败（由 API 依赖映射为 401/403）。"""


@dataclass(frozen=True)
class AuthContext:
    tenant_id: str
    user_id: str
    role: str


def create_access_token(
    *,
    tenant_id: str,
    user_id: str,
    role: str,
    expires_minutes: int | None = None,
) -> str:
    """签发 JWT。claim：sub / tenant_id / role / exp（architecture §4）。"""
    if role not in ROLES:
        raise ValueError(f"非法角色: {role}")
    minutes = expires_minutes if expires_minutes is not None else settings.access_token_expire_minutes
    expire = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    claims = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "exp": expire,
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> AuthContext:
    """校验 token 并提取身份；任何失败（签名/过期/缺 claim/非法角色）抛 AuthError。"""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require_exp": True},
        )
    except JWTError as exc:
        raise AuthError("invalid or expired token") from exc

    tenant_id = payload.get("tenant_id")
    user_id = payload.get("sub")
    role = payload.get("role")
    if not tenant_id or not user_id or role not in ROLES:
        raise AuthError("token missing or invalid identity claims")
    return AuthContext(tenant_id=str(tenant_id), user_id=str(user_id), role=role)


def hash_password(password: str) -> str:
    """PBKDF2-SHA256 加盐哈希（每次随机盐，同一密码两次结果不同）。"""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """常量时间比较；格式错误或参数非法时返回 False，不抛异常。"""
    try:
        _, iterations, salt, stored = encoded.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), stored)
    except (ValueError, TypeError):
        return False
