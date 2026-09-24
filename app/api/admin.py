"""管理端点：仅 admin 可访问，且强制 tenant 过滤（NFR3-02 / NFR3-03）。"""

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_roles
from app.core.database import get_session
from app.core.exceptions import ForbiddenError
from app.core.security import ROLE_ADMIN, ROLE_AGENT, AuthContext
from app.services import audit_service, dead_letter_service, handoff_service, identity_service, llm_usage_service

router = APIRouter(tags=["admin"])


class AgentReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=4000)


class CreateAdminRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=5, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    initial_password: str = Field(min_length=8, max_length=128)


def _handoff_json(row):
    return handoff_service.serialize(row)


@router.get("/admin/handoffs")
async def list_handoffs(auth: AuthContext = Depends(require_roles(ROLE_ADMIN, ROLE_AGENT)), session: AsyncSession = Depends(get_session)):
    rows = await handoff_service.list_for_tenant(session, uuid.UUID(auth.tenant_id))
    return {"handoffs": [_handoff_json(row) for row in rows]}


@router.post("/admin/handoffs/{handoff_id}/reply")
async def reply_handoff(handoff_id: uuid.UUID, payload: AgentReplyRequest,
                        auth: AuthContext = Depends(require_roles(ROLE_ADMIN, ROLE_AGENT)),
                        session: AsyncSession = Depends(get_session)):
    row = await handoff_service.reply(session, tenant_id=uuid.UUID(auth.tenant_id),
                                      handoff_id=handoff_id, content=payload.content)
    if row is None:
        raise ForbiddenError("人工会话不存在或不属于当前租户")
    return _handoff_json(row)


@router.post("/admin/handoffs/{handoff_id}/accept")
async def accept_handoff(handoff_id: uuid.UUID,
                         auth: AuthContext = Depends(require_roles(ROLE_ADMIN, ROLE_AGENT)),
                         session: AsyncSession = Depends(get_session)):
    row = await handoff_service.accept(session, tenant_id=uuid.UUID(auth.tenant_id), handoff_id=handoff_id)
    if row is None:
        raise ForbiddenError("人工会话不存在、不属于当前租户或已经结束")
    return _handoff_json(row)


@router.post("/admin/handoffs/{handoff_id}/request-close")
async def request_close_handoff(handoff_id: uuid.UUID,
                                auth: AuthContext = Depends(require_roles(ROLE_ADMIN, ROLE_AGENT)),
                                session: AsyncSession = Depends(get_session)):
    row = await handoff_service.request_close(session, tenant_id=uuid.UUID(auth.tenant_id), handoff_id=handoff_id)
    if row is None:
        raise ForbiddenError("人工会话不存在、不属于当前租户或已经结束")
    return _handoff_json(row)


@router.get("/admin/users")
async def list_users(
    auth: AuthContext = Depends(require_roles(ROLE_ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    """当前租户用户列表（批量展示脱敏 email/phone）。tenant 只取 token。"""
    rows = await identity_service.list_users(session, uuid.UUID(auth.tenant_id))
    return {"users": [identity_service.serialize_user(u, mask_pii_fields=True) for u in rows]}


@router.post("/admin/users", status_code=201)
async def create_admin_user(
    payload: CreateAdminRequest,
    auth: AuthContext = Depends(require_roles(ROLE_ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    tenant_id = uuid.UUID(auth.tenant_id)
    try:
        user = await identity_service.create_admin(
            session, tenant_id=tenant_id, email=payload.email,
            full_name=payload.full_name, initial_password=payload.initial_password,
        )
        await audit_service.record_audit(
            session, tenant_id=tenant_id, actor_id=auth.user_id,
            action="admin.user.create", target=f"user:{user.id}", outcome="success",
            detail={"role": "admin"},
        )
        await session.commit()
        await session.refresh(user)
    except IntegrityError as exc:
        await session.rollback()
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail="该邮箱已在当前租户中使用") from exc
    return {
        "user": identity_service.serialize_user(user),
        "login": {"tenant_id": auth.tenant_id, "email": user.email, "password": payload.initial_password},
    }


@router.get("/admin/audit-logs")
async def list_audit_logs(
    auth: AuthContext = Depends(require_roles(ROLE_ADMIN)),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=100, ge=1, le=1000),
):
    """当前租户审计日志（强制 tenant 过滤）。"""
    rows = await audit_service.list_audit_logs(session, uuid.UUID(auth.tenant_id), limit=limit)
    return {"audit_logs": [audit_service.serialize_audit(r) for r in rows]}


@router.get("/admin/llm-usage")
async def llm_usage(
    auth: AuthContext = Depends(require_roles(ROLE_ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    return {"conversations": await llm_usage_service.conversation_totals(session, uuid.UUID(auth.tenant_id))}


@router.get("/admin/dead-letters")
async def list_dead_letters(
    auth: AuthContext = Depends(require_roles(ROLE_ADMIN)),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=100, ge=1, le=1000),
):
    rows = await dead_letter_service.list_for_tenant(session, uuid.UUID(auth.tenant_id), limit)
    return {"dead_letters": [{
        "id": str(row.id), "trace_id": row.trace_id,
        "original_event_id": row.original_event_id,
        "original_subject": row.original_subject, "error_type": row.error_type,
        "error": row.error, "metadata": row.metadata_json,
        "status": row.status, "created_at": row.created_at,
        "replayed_at": row.replayed_at,
    } for row in rows]}


@router.post("/admin/dead-letters/{dead_letter_id}/replay", status_code=202)
async def replay_dead_letter(
    dead_letter_id: uuid.UUID,
    auth: AuthContext = Depends(require_roles(ROLE_ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    row = await dead_letter_service.replay(session, uuid.UUID(auth.tenant_id), dead_letter_id)
    return {"id": str(row.id), "status": row.status, "replayed_at": row.replayed_at}
