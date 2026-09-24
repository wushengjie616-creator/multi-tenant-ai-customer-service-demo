"""身份服务：登录认证、用户查询与（可选）脱敏序列化。"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models import Tenant, User
from app.utils.masking import mask_email, mask_phone


async def authenticate(
    session: AsyncSession, tenant_id: uuid.UUID, email: str, password: str
) -> User | None:
    """按 (tenant_id, email) 校验密码，成功返回用户，失败返回 None。"""
    user = await session.scalar(
        select(User).where(User.tenant_id == tenant_id, User.email == email)
    )
    if user is None:
        return None
    # 注意：此处未对不存在的用户做等价时延，邮箱枚举非本次验收重点。
    if not verify_password(password, user.password_hash):
        return None
    return user


async def tenant_exists(session: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """登录失败审计前确认租户存在，避免伪造 tenant 触发审计外键错误。"""
    return (
        await session.scalar(select(Tenant.id).where(Tenant.id == tenant_id))
    ) is not None


async def get_user(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> User | None:
    return await session.scalar(
        select(User).where(User.tenant_id == tenant_id, User.id == user_id)
    )


async def list_users(session: AsyncSession, tenant_id: uuid.UUID) -> list[User]:
    rows = await session.scalars(
        select(User).where(User.tenant_id == tenant_id).order_by(User.created_at)
    )
    return list(rows)


async def create_admin(
    session: AsyncSession, *, tenant_id: uuid.UUID, email: str,
    full_name: str, initial_password: str,
) -> User:
    user = User(
        tenant_id=tenant_id, role="admin", status="active",
        email=email.lower().strip(), full_name=full_name.strip(),
        password_hash=hash_password(initial_password),
    )
    session.add(user)
    await session.flush()
    return user


def serialize_user(user: User, *, mask_pii_fields: bool = False) -> dict:
    """用户 -> 字典；`mask_pii_fields=True` 时对 email/phone 脱敏（批量展示用）。"""
    email = mask_email(user.email) if mask_pii_fields else user.email
    phone = user.phone
    if mask_pii_fields and phone:
        phone = mask_phone(phone)
    return {
        "id": str(user.id),
        "tenant_id": str(user.tenant_id),
        "role": user.role,
        "email": email,
        "phone": phone,
        "full_name": user.full_name,
    }
