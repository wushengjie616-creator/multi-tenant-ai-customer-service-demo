"""财务、平台指令、提醒与人工转接 API。"""

import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.clients.finance_client import finance_client
from app.clients.platform_client import platform_client
from app.core.database import get_session
from app.core.circuit_breaker import CircuitOpenError
from app.core.exceptions import ForbiddenError
from app.core.security import AuthContext
from app.models import Reminder
from app.services import command_service, handoff_service, reminder_service
from app.services import audit_service, conversation_service, identity_service
from app.services.finance_service import safe_finance_result

router = APIRouter(tags=["business"])


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: uuid.UUID
    resource_id: str


class ImmediateCommandRequest(CommandRequest):
    arguments: dict = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=8, max_length=128)


class ReminderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: uuid.UUID
    content: str = Field(min_length=1, max_length=1000)
    run_at_local: str
    timezone: str = "Asia/Shanghai"
    repeat: str = "once"
    lead_time_minutes: int = Field(default=0, ge=0, le=10080)


class ReminderPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str | None = Field(default=None, min_length=1, max_length=1000)
    run_at_local: str | None = None
    timezone: str | None = None
    repeat: str | None = None
    lead_time_minutes: int | None = Field(default=None, ge=0, le=10080)
    version: int = Field(ge=1)


class HandoffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: uuid.UUID
    summary: str = ""
    reason: str = "explicit_request"
    attempted_actions: list[str] = []
    message: str | None = Field(default=None, min_length=1, max_length=4000)


class HandoffCloseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool


def _serialize_reminder(row: Reminder) -> dict:
    return {
        "id": str(row.id), "content": row.content, "status": row.status,
        "scheduled_for_at": row.scheduled_for_at, "next_run_at": row.next_run_at,
        "lead_time_minutes": row.lead_time_minutes, "timezone": row.timezone,
        "repeat": row.repeat, "version": row.version,
    }


@router.get("/finance/{kind}")
async def finance_query(kind: str, target_user_id: uuid.UUID | None = None, auth: AuthContext = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    target = target_user_id or uuid.UUID(auth.user_id)
    if auth.role == "user" and str(target) != auth.user_id:
        await audit_service.record_audit(session, tenant_id=uuid.UUID(auth.tenant_id), actor_id=auth.user_id, action=f"finance.{kind}", target=f"user:{target}", outcome="denied", detail={"reason": "ownership_denied"})
        await session.commit()
        raise ForbiddenError("不能查询其他用户的财务信息")
    if await identity_service.get_user(session, uuid.UUID(auth.tenant_id), target) is None:
        await audit_service.record_audit(session, tenant_id=uuid.UUID(auth.tenant_id), actor_id=auth.user_id, action=f"finance.{kind}", target=f"user:{target}", outcome="denied", detail={"reason": "target_not_in_tenant"})
        await session.commit()
        raise ForbiddenError("目标用户不属于当前租户")
    try:
        data = await finance_client.query(kind, auth.tenant_id, str(target))
        result = safe_finance_result(data)
        await audit_service.record_audit(session, tenant_id=uuid.UUID(auth.tenant_id), actor_id=auth.user_id, action=f"finance.{kind}", target=f"user:{target}", outcome="success")
    except (httpx.HTTPError, ValueError, TimeoutError, CircuitOpenError) as exc:
        result = safe_finance_result(None, error=type(exc).__name__)
        await audit_service.record_audit(session, tenant_id=uuid.UUID(auth.tenant_id), actor_id=auth.user_id, action=f"finance.{kind}", target=f"user:{target}", outcome="failed", detail={"error": type(exc).__name__})
    await session.commit()
    return result


@router.post("/commands/close-auto-renew", status_code=202)
async def close_auto_renew(payload: CommandRequest, auth: AuthContext = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    if not await conversation_service.conversation_belongs_to_user(session, uuid.UUID(auth.tenant_id), payload.conversation_id, uuid.UUID(auth.user_id)):
        raise ForbiddenError("会话不存在或不属于当前用户")
    confirmation = await command_service.propose(session, tenant_id=uuid.UUID(auth.tenant_id), user_id=uuid.UUID(auth.user_id), conversation_id=payload.conversation_id, action="close_auto_renew", resource_id=payload.resource_id, arguments={})
    return {"status": confirmation.status, "confirmation_id": str(confirmation.id), "expires_at": confirmation.expires_at}


@router.post("/commands/confirm/{confirmation_id}")
async def confirm_command(confirmation_id: uuid.UUID, auth: AuthContext = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    execution = await command_service.confirm_and_execute(session, confirmation_id=confirmation_id, tenant_id=uuid.UUID(auth.tenant_id), user_id=uuid.UUID(auth.user_id))
    return {"status": execution.status, "execution_id": str(execution.id), "result": execution.result}


@router.get("/platform/{kind}")
async def platform_query(kind: str, auth: AuthContext = Depends(get_current_user)):
    names = {
        "course-schedule": "course_schedule",
        "study-report": "study_report",
        "subscription-status": "subscription_status",
    }
    if kind not in names:
        raise HTTPException(status_code=404, detail="不支持的平台查询")
    try:
        return await platform_client.query(names[kind], auth.tenant_id, auth.user_id)
    except (httpx.HTTPError, ValueError, TimeoutError, CircuitOpenError) as exc:
        return {"status": "unavailable", "error": type(exc).__name__, "data": None}


@router.post("/commands/{action}")
async def execute_platform_command(
    action: str, payload: ImmediateCommandRequest,
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    allowed = {
        "submit-leave": "submit_leave",
        "update-course-reminder": "update_course_reminder",
        "open-auto-renew": "open_auto_renew",
    }
    if action not in allowed:
        raise HTTPException(status_code=404, detail="不支持的平台指令")
    if not await conversation_service.conversation_belongs_to_user(
        session, uuid.UUID(auth.tenant_id), payload.conversation_id, uuid.UUID(auth.user_id)
    ):
        raise ForbiddenError("会话不存在或不属于当前用户")
    execution = await command_service.execute_idempotent(
        session, tenant_id=uuid.UUID(auth.tenant_id), user_id=uuid.UUID(auth.user_id),
        action=allowed[action], resource_id=payload.resource_id,
        arguments=payload.arguments, client_key=payload.idempotency_key,
    )
    return {"status": execution.status, "execution_id": str(execution.id), "result": execution.result}


@router.post("/reminders", status_code=201)
async def create_reminder(payload: ReminderRequest, auth: AuthContext = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    if not await conversation_service.conversation_belongs_to_user(session, uuid.UUID(auth.tenant_id), payload.conversation_id, uuid.UUID(auth.user_id)):
        raise ForbiddenError("会话不存在或不属于当前用户")
    try:
        run_at = reminder_service.compute_next_run(
            run_at_local=payload.run_at_local, timezone_name=payload.timezone,
            repeat=payload.repeat, lead_time_minutes=payload.lead_time_minutes,
            now=datetime.now(timezone.utc),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    existing = await session.scalar(select(Reminder).where(
        Reminder.tenant_id == uuid.UUID(auth.tenant_id),
        Reminder.user_id == uuid.UUID(auth.user_id),
        Reminder.content == payload.content,
        Reminder.next_run_at == run_at,
        Reminder.status == "active",
    ))
    if existing is not None:
        raise HTTPException(status_code=409, detail="已存在内容和时间相同的提醒，本次不会重复创建。")
    scheduled_for = run_at + timedelta(minutes=payload.lead_time_minutes)
    row = Reminder(
        tenant_id=uuid.UUID(auth.tenant_id), user_id=uuid.UUID(auth.user_id),
        conversation_id=payload.conversation_id, content=payload.content,
        timezone=payload.timezone, repeat=payload.repeat,
        scheduled_for_at=scheduled_for, lead_time_minutes=payload.lead_time_minutes,
        next_run_at=run_at,
    )
    session.add(row); await session.commit(); await session.refresh(row)
    return _serialize_reminder(row)


@router.get("/reminders")
async def list_reminders(auth: AuthContext = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    rows = await reminder_service.list_owned(session, uuid.UUID(auth.tenant_id), uuid.UUID(auth.user_id))
    return {"reminders": [_serialize_reminder(r) for r in rows]}


@router.delete("/reminders/{reminder_id}")
async def cancel_reminder(reminder_id: uuid.UUID, auth: AuthContext = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    row = await session.get(Reminder, reminder_id)
    if row is None or str(row.tenant_id) != auth.tenant_id or str(row.user_id) != auth.user_id:
        raise ForbiddenError("提醒不存在或不属于当前用户")
    row.status = "cancelled"; row.version += 1; await session.commit()
    return {"id": str(row.id), "status": row.status, "version": row.version}


@router.patch("/reminders/{reminder_id}")
async def update_reminder(reminder_id: uuid.UUID, payload: ReminderPatch, auth: AuthContext = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    row = await session.get(Reminder, reminder_id)
    if row is None or str(row.tenant_id) != auth.tenant_id or str(row.user_id) != auth.user_id:
        raise ForbiddenError("提醒不存在或不属于当前用户")
    if row.version != payload.version or row.status != "active":
        raise HTTPException(status_code=409, detail="提醒已被修改或不可编辑")
    if payload.timezone is not None and payload.run_at_local is None:
        raise HTTPException(status_code=422, detail="修改时区时必须同时提供 run_at_local")
    if payload.content is not None:
        row.content = payload.content
    if payload.run_at_local is not None:
        try:
            lead = payload.lead_time_minutes if payload.lead_time_minutes is not None else row.lead_time_minutes
            row.next_run_at = reminder_service.compute_next_run(
                run_at_local=payload.run_at_local, timezone_name=payload.timezone or row.timezone,
                repeat=payload.repeat or row.repeat, lead_time_minutes=lead,
                now=datetime.now(timezone.utc),
            )
            row.scheduled_for_at = row.next_run_at + timedelta(minutes=lead)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.timezone is not None:
        row.timezone = payload.timezone
    if payload.repeat is not None:
        if payload.repeat not in reminder_service.SUPPORTED_REPEATS:
            raise HTTPException(status_code=422, detail="unsupported repeat rule")
        row.repeat = payload.repeat
    if payload.lead_time_minutes is not None:
        if payload.run_at_local is None:
            row.lead_time_minutes = payload.lead_time_minutes
            row.next_run_at = row.scheduled_for_at - timedelta(minutes=payload.lead_time_minutes)
            if row.next_run_at <= datetime.now(timezone.utc):
                raise HTTPException(status_code=422, detail="提前时间会导致通知时间已过期")
        else:
            row.lead_time_minutes = payload.lead_time_minutes
    row.version += 1
    await session.commit()
    return _serialize_reminder(row)


@router.post("/handoffs", status_code=202)
async def create_handoff(payload: HandoffRequest, auth: AuthContext = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    if not await conversation_service.conversation_belongs_to_user(session, uuid.UUID(auth.tenant_id), payload.conversation_id, uuid.UUID(auth.user_id)):
        raise ForbiddenError("会话不存在或不属于当前用户")
    row = await handoff_service.create_handoff(session, tenant_id=uuid.UUID(auth.tenant_id), user_id=uuid.UUID(auth.user_id), conversation_id=payload.conversation_id, summary=payload.summary, reason=payload.reason, context={"attempted_actions": payload.attempted_actions})
    if payload.message:
        await handoff_service.add_customer_message(session, handoff=row, content=payload.message)
    return handoff_service.serialize(row)


@router.get("/handoffs/active")
async def active_handoff(conversation_id: uuid.UUID,
                         auth: AuthContext = Depends(get_current_user),
                         session: AsyncSession = Depends(get_session)):
    if not await conversation_service.conversation_belongs_to_user(
        session, uuid.UUID(auth.tenant_id), conversation_id, uuid.UUID(auth.user_id)
    ):
        raise ForbiddenError("会话不存在或不属于当前用户")
    row = await handoff_service.get_active(session, uuid.UUID(auth.tenant_id), conversation_id)
    return {"handoff": handoff_service.serialize(row) if row else None}


@router.post("/handoffs/{handoff_id}/close-response")
async def handoff_close_response(handoff_id: uuid.UUID, payload: HandoffCloseResponse,
                                 auth: AuthContext = Depends(get_current_user),
                                 session: AsyncSession = Depends(get_session)):
    row = await handoff_service.respond_to_close(
        session, tenant_id=uuid.UUID(auth.tenant_id), user_id=uuid.UUID(auth.user_id),
        handoff_id=handoff_id, confirm=payload.confirm,
    )
    if row is None:
        raise ForbiddenError("待确认的人工会话不存在或不属于当前用户")
    return handoff_service.serialize(row)
