"""审计日志服务（NFR4-05）：记录与查询，落地前统一脱敏。"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_trace_id
from app.models import AuditLog
from app.utils.masking import mask_pii, mask_value


async def record_audit(
    session: AsyncSession,
    *,
    tenant_id,
    actor_id,
    action: str,
    target: str,
    outcome: str,
    detail: dict | None = None,
) -> None:
    """新增一条审计记录（不 commit，由调用方决定是否随业务事务提交）。"""
    session.add(
        AuditLog(
            trace_id=get_trace_id() or "",
            tenant_id=tenant_id,
            actor_id=str(actor_id),
            action=action,
            target=mask_pii(str(target)),
            outcome=outcome,
            detail=mask_value(detail) if detail else {},
        )
    )


async def list_audit_logs(
    session: AsyncSession, tenant_id, *, limit: int = 100
) -> list[AuditLog]:
    """查询当前租户的审计日志（强制 tenant 过滤，返回最近 limit 条）。"""
    rows = await session.scalars(
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    return list(rows)


def serialize_audit(row: AuditLog) -> dict:
    return {
        "id": str(row.id),
        "trace_id": row.trace_id,
        "tenant_id": str(row.tenant_id),
        "actor_id": row.actor_id,
        "action": row.action,
        "target": row.target,
        "outcome": row.outcome,
        "detail": row.detail,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
