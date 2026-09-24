"""FastAPI 认证/授权依赖：身份与租户只来自已验证 token（NFR3-01 / NFR3-02 / NFR3-06）。"""

import uuid

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.logging import set_tenant_id
from app.core.security import AuthContext, AuthError, decode_token

_bearer = HTTPBearer(auto_error=False)


def authenticate_bearer_token(token: str) -> AuthContext:
    """供 HTTP 与 WebSocket 共用的 token 校验，不依赖传输对象。"""
    try:
        auth = decode_token(token)
        uuid.UUID(auth.tenant_id)
        uuid.UUID(auth.user_id)
    except (AuthError, ValueError, TypeError):
        raise UnauthorizedError("无效或过期的 token")
    return auth


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AuthContext:
    """解析 Authorization: Bearer <jwt>，失败返回 401。"""
    if credentials is None:
        raise UnauthorizedError("缺少认证凭据")
    auth = authenticate_bearer_token(credentials.credentials)
    set_tenant_id(auth.tenant_id)
    return auth


def require_roles(*roles: str):
    """RBAC 依赖工厂：当前用户角色不在白名单时返回 403。"""

    async def _check(auth: AuthContext = Depends(get_current_user)) -> AuthContext:
        if auth.role not in roles:
            raise ForbiddenError("无权执行该操作")
        return auth

    return _check
