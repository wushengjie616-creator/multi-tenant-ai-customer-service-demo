"""高风险平台操作确认与幂等执行。"""

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.platform_client import platform_client
from app.core.exceptions import ForbiddenError, NotFoundError
from app.models import Confirmation, ToolExecution


def _hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def propose(session: AsyncSession, *, tenant_id, user_id, conversation_id, action: str, resource_id: str, arguments: dict) -> Confirmation:
    binding = {"tenant_id": str(tenant_id), "user_id": str(user_id), "conversation_id": str(conversation_id), "action": action, "resource_id": resource_id, "arguments": arguments}
    args_hash = _hash(binding)
    now = datetime.now(timezone.utc)
    existing = await session.scalar(
        select(Confirmation).where(
            Confirmation.args_hash == args_hash,
            Confirmation.status == "pending_confirmation",
            Confirmation.expires_at > now,
        ).order_by(Confirmation.created_at.desc())
    )
    if existing is not None:
        return existing
    confirmation = Confirmation(tenant_id=tenant_id, user_id=user_id, conversation_id=conversation_id, action=action, resource_id=resource_id, args_hash=args_hash, arguments=arguments, expires_at=now + timedelta(minutes=5))
    session.add(confirmation)
    await session.commit(); await session.refresh(confirmation)
    return confirmation


async def confirm_and_execute(session: AsyncSession, *, confirmation_id: uuid.UUID, tenant_id: uuid.UUID, user_id: uuid.UUID) -> ToolExecution:
    now = datetime.now(timezone.utc)
    changed = await session.execute(update(Confirmation).where(Confirmation.id == confirmation_id, Confirmation.tenant_id == tenant_id, Confirmation.user_id == user_id, Confirmation.status == "pending_confirmation", Confirmation.expires_at > now).values(status="executing").returning(Confirmation))
    confirmation = changed.scalar_one_or_none()
    if confirmation is None:
        existing = await session.get(Confirmation, confirmation_id)
        if existing is None:
            raise NotFoundError("确认请求不存在")
        if existing.tenant_id != tenant_id or existing.user_id != user_id:
            raise ForbiddenError("确认请求不属于当前用户")
        execution_key = _hash({"confirmation_id": str(existing.id), "args_hash": existing.args_hash})
        execution = await session.scalar(
            select(ToolExecution).where(ToolExecution.idempotency_key == execution_key)
        )
        if execution is None:
            # Compatibility for confirmations executed before keys became confirmation-scoped.
            execution = await session.scalar(
                select(ToolExecution).where(ToolExecution.idempotency_key == existing.args_hash)
            )
        if execution is not None:
            return execution
        raise ForbiddenError("确认请求已使用或已过期")
    execution_key = _hash({"confirmation_id": str(confirmation.id), "args_hash": confirmation.args_hash})
    execution = ToolExecution(tenant_id=tenant_id, user_id=user_id, action=confirmation.action, resource_id=confirmation.resource_id, idempotency_key=execution_key)
    prior_execution = await session.scalar(
        select(ToolExecution).where(ToolExecution.idempotency_key == execution_key)
    )
    if prior_execution is not None:
        confirmation.status = prior_execution.status
        await session.commit()
        return prior_execution
    session.add(execution); await session.commit()
    try:
        result = await platform_client.execute(confirmation.action, {**confirmation.arguments, "resource_id": confirmation.resource_id}, execution_key)
        execution.status = "succeeded"; execution.result = result; confirmation.status = "succeeded"
    except Exception as exc:
        execution.status = "failed"; execution.result = {"error": type(exc).__name__}; confirmation.status = "failed"
    await session.commit(); await session.refresh(execution)
    return execution


async def execute_idempotent(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID,
    action: str, resource_id: str, arguments: dict, client_key: str,
) -> ToolExecution:
    """Execute a low-risk command once, binding the caller key to its identity and action."""
    key = _hash({
        "tenant_id": str(tenant_id), "user_id": str(user_id), "action": action,
        "client_key": client_key,
    })
    existing = await session.scalar(select(ToolExecution).where(ToolExecution.idempotency_key == key))
    if existing is not None:
        return existing
    execution = ToolExecution(
        tenant_id=tenant_id, user_id=user_id, action=action,
        resource_id=resource_id, idempotency_key=key,
    )
    session.add(execution)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        concurrent = await session.scalar(select(ToolExecution).where(ToolExecution.idempotency_key == key))
        if concurrent is not None:
            return concurrent
        raise
    try:
        execution.result = await platform_client.execute(
            action, {**arguments, "resource_id": resource_id}, key,
        )
        execution.status = "succeeded"
    except Exception as exc:
        execution.status = "failed"
        execution.result = {"error": type(exc).__name__}
    await session.commit()
    await session.refresh(execution)
    return execution
