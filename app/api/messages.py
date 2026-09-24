"""消息 HTTP 入口：webhook 入站与历史查询（身份/租户只来自 JWT）。"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.database import get_session
from app.core.exceptions import ForbiddenError
from app.core.logging import get_logger
from app.core.security import ROLE_USER, AuthContext
from app.schemas.message import InboundMessage
from app.services import audit_service, conversation_service

router = APIRouter(tags=["messages"])
log = get_logger(__name__)


def _to_uuid(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError):
        raise ForbiddenError(f"{field} 不是合法 UUID")


@router.post("/webhooks/im/messages", status_code=202)
async def inbound_message(
    msg: InboundMessage,
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """IM webhook 入站：快速 ACK，业务异步处理。

    身份与租户只取自已验证 token（NFR3-06）；请求体/LLM 提供的 tenant_id / user_id
    不能覆盖认证身份 —— 不一致直接 403，一致则以 token 身份为准。
    """
    if msg.tenant_id and msg.tenant_id != auth.tenant_id:
        raise ForbiddenError("tenant_id 与认证身份不一致")
    if msg.user_id and msg.user_id != auth.user_id:
        raise ForbiddenError("user_id 与认证身份不一致")

    inbound = InboundMessage(
        message_id=msg.message_id,
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        conversation_id=msg.conversation_id,
        content=msg.content,
        timestamp=msg.timestamp,
    )
    return await conversation_service.ingest_message(session, inbound)


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: str,
    after_message_id: str | None = Query(default=None, max_length=128),
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """重连补取消息：会话必须属于认证租户，否则 403 并记审计。"""
    tenant_uuid = uuid.UUID(auth.tenant_id)
    conv_uuid = _to_uuid(conversation_id, "conversation_id")
    if auth.role == ROLE_USER:
        allowed = await conversation_service.conversation_belongs_to_user(
            session, tenant_uuid, conv_uuid, uuid.UUID(auth.user_id)
        )
    else:
        allowed = await conversation_service.conversation_belongs_to_tenant(
            session, tenant_uuid, conv_uuid
        )
    if not allowed:
        await audit_service.record_audit(
            session,
            tenant_id=tenant_uuid,
            actor_id=auth.user_id,
            action="messages.list",
            target=f"conversation:{conversation_id}",
            outcome="denied",
            detail={"reason": "not_found_or_cross_tenant"},
        )
        await session.commit()
        raise ForbiddenError("会话不存在或不属于当前租户")
    if after_message_id:
        rows = await conversation_service.list_messages_after(
            session, tenant_uuid, conv_uuid, after_message_id
        )
    else:
        rows = await conversation_service.list_messages(session, tenant_uuid, conv_uuid)
    return {"conversation_id": conversation_id, "messages": rows}
