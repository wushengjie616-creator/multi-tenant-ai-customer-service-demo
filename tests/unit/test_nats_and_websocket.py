"""P2 NATS consumer 可靠性参数与 WebSocket tenant fan-out。"""

from app.api.websocket import ConnectionManager, build_reply_events
from app.core import nats as nats_core


class _FakeJetStream:
    def __init__(self):
        self.kwargs = None
        self.published = None

    async def subscribe(self, subject, **kwargs):
        self.subject = subject
        self.kwargs = kwargs

    async def publish(self, subject, data, **kwargs):
        self.published = (subject, data, kwargs)


async def test_subscribe_uses_explicit_ack_and_bounded_delivery():
    js = _FakeJetStream()

    async def callback(msg):
        return None

    await nats_core.subscribe(js, "im.inbound", "worker", callback)
    config = js.kwargs["config"]
    assert config.max_deliver == 5
    assert config.ack_wait == 150
    assert config.filter_subject == "im.inbound"
    assert js.kwargs["manual_ack"] is True


async def test_publish_can_bind_jetstream_deduplication_to_event_id():
    js = _FakeJetStream()
    await nats_core.publish_bytes(
        js, "im.outbound", b"payload", message_id="event-123"
    )
    assert js.published == (
        "im.outbound",
        b"payload",
        {"stream": "EVENTS", "headers": {"Nats-Msg-Id": "event-123"}},
    )


class _FakeWebSocket:
    def __init__(self):
        self.accepted = False
        self.events = []

    async def accept(self):
        self.accepted = True

    async def send_json(self, event):
        self.events.append(event)


async def test_websocket_fanout_is_scoped_by_tenant_and_conversation():
    manager = ConnectionManager()
    tenant_a = _FakeWebSocket()
    tenant_b = _FakeWebSocket()
    await manager.connect("tenant-a", "same-conversation", tenant_a)
    await manager.connect("tenant-b", "same-conversation", tenant_b)

    event = {"type": "reply.end", "payload": {"content": "only A"}}
    await manager.push("tenant-a", "same-conversation", event)

    assert tenant_a.events == [event]
    assert tenant_b.events == []


def test_reply_stream_has_start_chunks_end_and_reconstructs_content():
    envelope = {
        "event_id": "e1",
        "trace_id": "t1",
        "tenant_id": "tenant-a",
        "occurred_at": "2026-09-23T00:00:00Z",
        "schema_version": "1.0",
        "payload": {
            "message_id": "reply-1",
            "conversation_id": "conversation-1",
            "content": "abcdefg",
            "in_reply_to": "input-1",
        },
    }
    events = build_reply_events(envelope, chunk_size=3)
    assert [event["type"] for event in events] == [
        "reply.start",
        "reply.chunk",
        "reply.chunk",
        "reply.chunk",
        "reply.end",
    ]
    assert "".join(event["payload"]["delta"] for event in events[1:-1]) == "abcdefg"
    assert events[-1]["payload"]["content"] == "abcdefg"
    assert [event["payload"].get("sequence") for event in events[1:-1]] == [0, 1, 2]
