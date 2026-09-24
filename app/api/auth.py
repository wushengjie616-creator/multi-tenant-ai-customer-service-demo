"""认证端点：登录签发 JWT、查询当前用户。"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.database import get_session
from app.core.exceptions import NotFoundError, UnauthorizedError
from app.core.security import AuthContext, create_access_token
from app.schemas.auth import LoginRequest, TokenResponse, UserProfile
from app.services import audit_service, identity_service
from app.utils.masking import mask_email

router = APIRouter(tags=["auth"])


def _to_uuid(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError):
        raise UnauthorizedError(f"{field} 不是合法 UUID")


@router.post("/auth/login", response_model=TokenResponse)
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_session)):
    """邮箱 + 密码 + 租户 认证，签发 JWT。身份/租户只进 token（architecture §4）。"""
    tenant_uuid = _to_uuid(payload.tenant_id, "tenant_id")
    user = await identity_service.authenticate(session, tenant_uuid, payload.email, payload.password)
    if user is None:
        # audit_logs 受 tenant 外键保护；未知 tenant 没有合法归属，不能强行落库。
        if await identity_service.tenant_exists(session, tenant_uuid):
            await audit_service.record_audit(
                session,
                tenant_id=tenant_uuid,
                actor_id="",
                action="auth.login",
                target=f"email:{mask_email(payload.email)}",
                outcome="denied",
                detail={"reason": "invalid_credentials"},
            )
            await session.commit()
        raise UnauthorizedError("邮箱或密码错误")

    token = create_access_token(
        tenant_id=str(user.tenant_id), user_id=str(user.id), role=user.role
    )
    await audit_service.record_audit(
        session,
        tenant_id=user.tenant_id,
        actor_id=str(user.id),
        action="auth.login",
        target=f"user:{user.id}",
        outcome="success",
    )
    await session.commit()
    return {
        "access_token": token,
        "user": identity_service.serialize_user(user),
    }


@router.get("/users/me", response_model=UserProfile)
async def me(
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """返回当前认证用户自己的完整画像（owner 有权看自己的 PII）。"""
    user = await identity_service.get_user(
        session, uuid.UUID(auth.tenant_id), uuid.UUID(auth.user_id)
    )
    if user is None:
        raise NotFoundError("用户不存在")
    return identity_service.serialize_user(user)
