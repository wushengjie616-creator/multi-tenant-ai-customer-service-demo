"""Explicitly gated helpers for the local customer-facing demo."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_session
from app.core.exceptions import AppError, DependencyError
from app.core.security import ROLE_ADMIN, ROLE_USER, create_access_token, hash_password
from app.api.dependencies import require_roles
from app.core.security import AuthContext
from app.models import Conversation, Tenant, User
from app.demo_catalog import demo_tenant_catalog

router = APIRouter(prefix="/demo", tags=["demo"])

Feature = Literal[
    "knowledge", "assistant", "learning", "finance",
    "services", "reminders", "handoff", "operations",
]
ALL_DEMO_FEATURES = [
    "knowledge", "assistant", "learning", "finance",
    "services", "reminders", "handoff", "operations",
]


async def _ensure_demo_customer(session: AsyncSession, tenant: Tenant, user_id: uuid.UUID | None = None) -> tuple[User, Conversation]:
    if user_id is not None:
        customer = await session.scalar(select(User).where(
            User.id == user_id, User.tenant_id == tenant.id, User.role == ROLE_USER
        ))
        if customer is None:
            raise AppError("模拟客户不存在或不属于该租户", code="FORBIDDEN", status_code=403)
    else:
        customer = await session.scalar(select(User).where(
            User.tenant_id == tenant.id, User.role == ROLE_USER
        ).order_by(User.created_at))
    if customer is None:
        customer = User(
            id=uuid.uuid4(), tenant_id=tenant.id,
            email=f"customer-{uuid.uuid4().hex[:10]}@demo.local",
            full_name="演示客户", role=ROLE_USER, status="active",
            password_hash=hash_password(uuid.uuid4().hex),
        )
        session.add(customer); await session.flush()
    conversation = await session.scalar(select(Conversation).where(
        Conversation.tenant_id == tenant.id, Conversation.user_id == customer.id
    ).order_by(Conversation.created_at))
    if conversation is None:
        conversation = Conversation(id=uuid.uuid4(), tenant_id=tenant.id, user_id=customer.id)
        session.add(conversation); await session.flush()
    return customer, conversation


async def _ensure_demo_admin(session: AsyncSession, tenant: Tenant) -> User:
    admin = await session.scalar(select(User).where(
        User.tenant_id == tenant.id, User.role == ROLE_ADMIN
    ).order_by(User.created_at))
    if admin is None:
        admin = User(
            id=uuid.uuid4(), tenant_id=tenant.id,
            email=f"platform-admin-{uuid.uuid4().hex[:8]}@demo.local",
            full_name="平台演示管理员", role=ROLE_ADMIN, status="active",
            password_hash=hash_password(uuid.uuid4().hex),
        )
        session.add(admin); await session.flush()
    return admin


def _session_payload(tenant: Tenant, admin: User, customer: User, conversation: Conversation, *, mode: str = "mock") -> dict:
    return {
        "access_token": create_access_token(tenant_id=str(tenant.id), user_id=str(admin.id), role=ROLE_ADMIN),
        "tenant_id": str(tenant.id), "tenant_name": tenant.name, "user_id": str(admin.id),
        "features": tenant.features or ALL_DEMO_FEATURES,
        "customer_access_token": create_access_token(tenant_id=str(tenant.id), user_id=str(customer.id), role=ROLE_USER),
        "customer_user_id": str(customer.id), "customer_name": customer.full_name or "客户",
        "customer_conversation_id": str(conversation.id),
        "dependency_mode": mode,
    }


class DemoTenantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_name: str = Field(min_length=2, max_length=100)
    admin_name: str = Field(min_length=1, max_length=100)
    admin_email: str = Field(min_length=5, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    admin_password: str = Field(min_length=8, max_length=128)
    features: list[Feature] = Field(min_length=1)


class PlatformSessionRequest(BaseModel):
    customer_user_id: uuid.UUID | None = None


@router.get("/config")
async def demo_config():
    """Return non-secret values used to prefill the local UI."""
    if not settings.demo_mode:
        raise AppError("演示功能未启用", code="NOT_FOUND", status_code=404)
    return {
        "tenant_id": settings.demo_tenant_id,
        "tenant_name": settings.demo_tenant_name,
        "admin_email": settings.demo_tenant_admin_email,
        "admin_conversation_id": settings.demo_admin_conversation_id,
        "max_upload_chars": settings.demo_max_upload_chars,
        "available_features": ALL_DEMO_FEATURES,
    }


@router.get("/platform/tenants")
async def platform_tenants(session: AsyncSession = Depends(get_session)):
    if not settings.demo_mode:
        raise AppError("演示功能未启用", code="NOT_FOUND", status_code=404)
    catalog = demo_tenant_catalog()
    catalog_by_id = {item["id"]: item for item in catalog}
    curated = list((await session.scalars(
        select(Tenant).where(Tenant.id.in_(catalog_by_id))
    )).all())
    # Test databases may not run the demo bootstrap. In that case only, retain
    # the generic listing behavior used by endpoint integration tests.
    tenants = sorted(curated, key=lambda item: catalog_by_id[item.id]["code"]) if curated else list(
        (await session.scalars(select(Tenant).order_by(Tenant.created_at))).all()
    )
    result = []
    for tenant in tenants:
        user_count = await session.scalar(select(func.count()).select_from(User).where(User.tenant_id == tenant.id))
        conversation_count = await session.scalar(select(func.count()).select_from(Conversation).where(Conversation.tenant_id == tenant.id))
        customers = list((await session.scalars(select(User).where(
            User.tenant_id == tenant.id, User.role == ROLE_USER
        ).order_by(User.created_at))).all())
        meta = catalog_by_id.get(tenant.id, {})
        result.append({"tenant_id": str(tenant.id),
                       "tenant_code": meta.get("code", str(tenant.id)[:8]),
                       "tenant_name": tenant.name,
                       "scenario": meta.get("scenario", "自定义租户"),
                       "features": tenant.features or [], "user_count": user_count,
                       "conversation_count": conversation_count, "status": tenant.status,
                       "customers": [{"user_id": str(user.id), "full_name": user.full_name or "模拟客户", "email": user.email} for user in customers]})
    return {"tenants": result}


@router.post("/platform/tenants/{tenant_id}/session")
async def platform_tenant_session(tenant_id: uuid.UUID, payload: PlatformSessionRequest | None = None, session: AsyncSession = Depends(get_session)):
    if not settings.demo_mode:
        raise AppError("演示功能未启用", code="NOT_FOUND", status_code=404)
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise AppError("租户不存在", code="NOT_FOUND", status_code=404)
    admin = await _ensure_demo_admin(session, tenant)
    customer, conversation = await _ensure_demo_customer(session, tenant, payload.customer_user_id if payload else None)
    await session.commit()
    return _session_payload(tenant, admin, customer, conversation)


@router.post("/tenants", status_code=201)
async def create_demo_tenant(payload: DemoTenantRequest, session: AsyncSession = Depends(get_session)):
    """Create an isolated local tenant and its first admin in demo mode."""
    if not settings.demo_mode:
        raise AppError("演示功能未启用", code="NOT_FOUND", status_code=404)
    features = list(dict.fromkeys(payload.features))
    tenant = Tenant(id=uuid.uuid4(), name=payload.tenant_name.strip(), features=features)
    try:
        session.add(tenant)
        await session.flush()
        admin = User(
            id=uuid.uuid4(), tenant_id=tenant.id,
            email=payload.admin_email.lower().strip(),
            full_name=payload.admin_name.strip(), role=ROLE_ADMIN,
            status="active", password_hash=hash_password(payload.admin_password),
        )
        session.add(admin)
        await session.flush()
        conversation = Conversation(id=uuid.uuid4(), tenant_id=tenant.id, user_id=admin.id)
        customer_profiles = [
            ("林晓晨家长", "lin-xiaochen"),
            ("周雨桐家长", "zhou-yutong"),
            ("陈知远家长", "chen-zhiyuan"),
            ("许安然家长", "xu-anran"),
        ]
        customers = [
            User(
                id=uuid.uuid4(), tenant_id=tenant.id,
                email=f"{email}-{tenant.id.hex[:6]}@demo.local",
                full_name=full_name, role=ROLE_USER, status="active",
                password_hash=hash_password(uuid.uuid4().hex),
            )
            for full_name, email in customer_profiles
        ]
        customer = customers[0]
        session.add_all([conversation, *customers])
        await session.flush()
        customer_conversations = [
            Conversation(id=uuid.uuid4(), tenant_id=tenant.id, user_id=item.id)
            for item in customers
        ]
        customer_conversation = customer_conversations[0]
        session.add_all(customer_conversations)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="租户名或管理员邮箱已存在") from exc
    return {
        "access_token": create_access_token(tenant_id=str(tenant.id), user_id=str(admin.id), role=ROLE_ADMIN),
        "token_type": "bearer", "tenant_id": str(tenant.id), "tenant_name": tenant.name,
        "user_id": str(admin.id), "conversation_id": str(conversation.id),
        "customer_access_token": create_access_token(tenant_id=str(tenant.id), user_id=str(customer.id), role=ROLE_USER),
        "customer_user_id": str(customer.id), "customer_name": customer.full_name or "客户",
        "customer_conversation_id": str(customer_conversation.id),
        "features": features, "dependency_mode": "configured",
    }


@router.post("/workbench-session")
async def restore_workbench_session(
    auth: AuthContext = Depends(require_roles(ROLE_ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    if not settings.demo_mode:
        raise AppError("演示功能未启用", code="NOT_FOUND", status_code=404)
    tenant = await session.get(Tenant, uuid.UUID(auth.tenant_id))
    admin = await session.get(User, uuid.UUID(auth.user_id))
    if tenant is None or admin is None:
        raise AppError("租户或管理员不存在", code="NOT_FOUND", status_code=404)
    customer, conversation = await _ensure_demo_customer(session, tenant)
    await session.commit()
    return _session_payload(tenant, admin, customer, conversation, mode="configured")


@router.post("/customer-session")
async def create_customer_session(session: AsyncSession = Depends(get_session)):
    """Issue a short-lived token for the fixed sample customer only in demo mode."""
    if not settings.demo_mode:
        raise AppError("演示功能未启用", code="NOT_FOUND", status_code=404)

    tenant_id = uuid.UUID(settings.demo_customer_tenant_id)
    user_id = uuid.UUID(settings.demo_customer_user_id)
    conversation_id = uuid.UUID(settings.demo_customer_conversation_id)
    tenant = await session.get(Tenant, tenant_id)
    user = await session.get(User, user_id)
    conversation = await session.get(Conversation, conversation_id)
    if (
        tenant is None
        or user is None
        or conversation is None
        or user.tenant_id != tenant_id
        or conversation.tenant_id != tenant_id
        or conversation.user_id != user_id
    ):
        raise DependencyError("演示数据尚未初始化")

    return {
        "access_token": create_access_token(
            tenant_id=str(tenant_id),
            user_id=str(user_id),
            role=ROLE_USER,
            expires_minutes=30,
        ),
        "token_type": "bearer",
        "tenant_id": str(tenant_id),
        "tenant_name": settings.demo_customer_tenant_name,
        "user_id": str(user_id),
        "customer_name": user.full_name or "家长",
        "conversation_id": str(conversation_id),
        "features": ALL_DEMO_FEATURES,
        "dependency_mode": "mock",
    }
