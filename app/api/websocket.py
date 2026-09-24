"""IM WebSocket 接入与出站推送消费者。"""

import asyncio
import json
import uuid
from collections import defaultdict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.dependencies import authenticate_bearer_token
from app.core import nats as nats_core
from app.core.database import async_session
from app.core.exceptions import UnauthorizedError
from app.core.logging import get_logger
from app.core.security import ROLE_USER
from app.schemas.event import EventType
from app.schemas.message import InboundMessage
from app.services import conversation_service

router = APIRouter(tags=["ws"])
log = get_logger(__name__)


class ConnectionManager:
    def __init__(self):
        self._connections: dict[tuple[str, str], set[WebSocket]] = defaultdict(set)

    async def connect(self, tenant_id: str, conversation_id: str, ws: WebSocket):
        await ws.accept()
        self._connections[(tenant_id, conversation_id)].add(ws)

    def disconnect(self, tenant_id: str, conversation_id: str, ws: WebSocket):
        key = (tenant_id, conversation_id)
        self._connections[key].discard(ws)
        if not self._connections[key]:
            self._connections.pop(key, None)

    async def push(self, tenant_id: str, conversation_id: str, event: dict):
        for ws in list(self._connections.get((tenant_id, conversation_id), ())):
            try:
                await ws.send_json(event)
            except Exception:
                self.disconnect(tenant_id, conversation_id, ws)


manager = ConnectionManager()


def build_reply_events(envelope: dict, *, chunk_size: int = 64) -> list[dict]:
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    payload = envelope.get("payload", {})
    common = {
        "event_id": envelope.get("event_id"),
        "trace_id": envelope.get("trace_id"),
        "tenant_id": envelope.get("tenant_id"),
        "occurred_at": envelope.get("occurred_at"),
        "schema_version": envelope.get("schema_version"),
    }
    base_payload = {
        "message_id": payload.get("message_id"),
        "conversation_id": payload.get("conversation_id"),
        "in_reply_to": payload.get("in_reply_to"),
    }
    if payload.get("reminder_id"):
        return [{**common, "type": EventType.REMINDER_TRIGGERED, "payload": payload}]
    if payload.get("handoff_id"):
        return [{**common, "type": EventType.HANDOFF_STATUS, "payload": payload}]

    content = payload.get("content") or ""
    if payload.get("streamed"):
        return [{
            **common,
            "type": EventType.REPLY_END,
            "payload": {**base_payload, "content": content},
        }]
    chunks = [content[index : index + chunk_size] for index in range(0, len(content), chunk_size)]
    events = [{**common, "type": EventType.REPLY_START, "payload": base_payload}]
    events.extend(
        {
            **common,
            "type": EventType.REPLY_CHUNK,
            "payload": {**base_payload, "sequence": sequence, "delta": chunk},
        }
        for sequence, chunk in enumerate(chunks)
    )
    events.append(
        {
            **common,
            "type": EventType.REPLY_END,
            "payload": {**base_payload, "content": content},
        }
    )
    return events


async def _on_outbound(msg):
    try:
        envelope = json.loads(msg.data)
        payload = envelope.get("payload", {})
        for event in build_reply_events(envelope):
            await manager.push(
                envelope.get("tenant_id"), payload.get("conversation_id"), event
            )
    except Exception:
        log.exception("outbound push failed")


async def _on_stream(msg):
    try:
        event = json.loads(msg.data)
        payload = event.get("payload", {})
        await manager.push(
            event.get("tenant_id"), payload.get("conversation_id"), event
        )
    except Exception:
        log.exception("live stream push failed")


async def run_outbound_consumer() -> None:
    nc = await nats_core.connect()
    await nats_core.subscribe_core(nc, nats_core.OUTBOUND_SUBJECT, _on_outbound)
    await nats_core.subscribe_core(nc, nats_core.OUTBOUND_STREAM_SUBJECT, _on_stream)
    log.info("outbound core fan-out ready (%s)", nats_core.OUTBOUND_SUBJECT)
    try:
        await asyncio.Future()
    finally:
        await nc.close()


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    authorization = ws.headers.get("authorization") or ""
    if not authorization.lower().startswith("bearer "):
        await ws.close(code=1008, reason="missing bearer token")
        return
    try:
        auth = authenticate_bearer_token(authorization.split(" ", 1)[1])
    except UnauthorizedError:
        await ws.close(code=1008, reason="invalid token")
        return
    if auth.role != ROLE_USER:
        await ws.close(code=1008, reason="websocket is limited to end users")
        return
    conversation_id = ws.query_params.get("conversation_id")
    try:
        conversation_uuid = uuid.UUID(conversation_id or "")
    except ValueError:
        await ws.close(code=1008, reason="invalid conversation_id")
        return
    async with async_session() as session:
        allowed = await conversation_service.conversation_belongs_to_user(
            session,
            uuid.UUID(auth.tenant_id),
            conversation_uuid,
            uuid.UUID(auth.user_id),
        )
    if not allowed:
        await ws.close(code=1008, reason="conversation is not accessible")
        return
    await manager.connect(auth.tenant_id, conversation_id, ws)
    after_message_id = ws.query_params.get("after_message_id")
    if after_message_id:
        async with async_session() as session:
            recovered = await conversation_service.list_messages_after(
                session,
                auth.tenant_id,
                conversation_id,
                after_message_id,
            )
        await ws.send_json(
            {
                "type": EventType.CONNECTION_RESUMED,
                "payload": {
                    "after_message_id": after_message_id,
                    "messages": recovered,
                },
            }
        )
    try:
        while True:
            raw = await ws.receive_text()
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": EventType.REPLY_ERROR, "message": "invalid json"})
                continue
            if frame.get("type") != EventType.MESSAGE_SEND:
                continue
            payload = frame.get("payload", {})
            if payload.get("tenant_id") not in (None, auth.tenant_id) or payload.get(
                "user_id"
            ) not in (None, auth.user_id):
                await ws.send_json(
                    {"type": EventType.REPLY_ERROR, "message": "identity mismatch"}
                )
                continue
            trusted_payload = {
                **payload,
                "tenant_id": auth.tenant_id,
                "user_id": auth.user_id,
                "conversation_id": conversation_id,
            }
            inbound = InboundMessage(**trusted_payload)
            async with async_session() as session:
                result = await conversation_service.ingest_message(session, inbound)
            await ws.send_json(
                {"type": EventType.MESSAGE_ACCEPTED, "message_id": inbound.message_id, **result}
            )
    except WebSocketDisconnect:
        manager.disconnect(auth.tenant_id, conversation_id, ws)
