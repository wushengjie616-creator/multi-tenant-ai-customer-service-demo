"""mock-im：模拟 IM 平台（WebSocket 收发、在线状态、推送记录）。

- WS /ws?conversation_id=... 客户端连接
- 收到客户端消息 -> 转发给 API /webhooks/im/messages
- 消费 im.outbound -> 推送给对应会话的 WS 客户端 + 记录推送
"""

import asyncio
import json
from collections import defaultdict
from contextlib import asynccontextmanager

import httpx
import nats
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

API_WEBHOOK_URL = "http://api:8000/webhooks/im/messages"
NATS_URL = "nats://nats:4222"
STREAM_NAME = "EVENTS"
OUTBOUND_SUBJECT = "im.outbound"

_connections: dict[str, set[WebSocket]] = defaultdict(set)
_pushes: list[dict] = []


async def _outbound_consumer():
    nc = await nats.connect(NATS_URL, allow_reconnect=True)
    js = nc.jetstream()
    # 等 EVENTS 流就绪（由 API 启动时创建）
    while True:
        try:
            await js.stream_info(STREAM_NAME)
            break
        except Exception:
            await asyncio.sleep(1)

    async def on_msg(msg):
        envelope = json.loads(msg.data)
        payload = envelope.get("payload", {})
        _pushes.append(payload)
        conv = payload.get("conversation_id")
        for ws in list(_connections.get(conv, ())):
            try:
                await ws.send_json({"type": "reply.end", **payload})
            except Exception:
                pass
        await msg.ack()

    await js.subscribe(
        OUTBOUND_SUBJECT, durable="im-outbound-mockim", cb=on_msg, stream=STREAM_NAME
    )
    await asyncio.Future()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_outbound_consumer())
    yield
    task.cancel()


app = FastAPI(title="mock-im", lifespan=lifespan)


async def forward_inbound(payload: dict, token: str, *, client_factory=httpx.AsyncClient):
    """Forward a mock customer message through the same authenticated API boundary."""
    async with client_factory(timeout=10.0) as client:
        await client.post(
            API_WEBHOOK_URL,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mock-im"}


@app.get("/pushes")
async def pushes():
    return {"pushes": _pushes}


@app.websocket("/ws")
async def ws(ws: WebSocket):
    conversation_id = ws.query_params.get("conversation_id") or "default"
    token = ws.query_params.get("token") or ""
    if not token:
        await ws.close(code=1008, reason="missing customer token")
        return
    await ws.accept()
    _connections[conversation_id].add(ws)
    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            payload = data.get("payload", data)
            await forward_inbound(payload, token)
            await ws.send_json(
                {"type": "message.accepted", "message_id": payload.get("message_id")}
            )
    except WebSocketDisconnect:
        _connections[conversation_id].discard(ws)
