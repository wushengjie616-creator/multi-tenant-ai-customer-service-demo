"""P2 NATS consumer 可靠性参数与 WebSocket tenant fan-out。"""

from types import SimpleNamespace

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


class _FakeNatsConnection:
    def __init__(self):
        self.subscriptions = []

    async def subscribe(self, subject, cb):
        self.subscriptions.append((subject, cb))
        return object()


class _FakeMigratingJetStream:
    def __init__(self):
        self.deleted = None
        self.pull_kwargs = None

    async def consumer_info(self, stream, durable):
        return SimpleNamespace(
            config=SimpleNamespace(deliver_subject="_INBOX.old-push"),
            ack_floor=SimpleNamespace(stream_seq=42),
        )

    async def delete_consumer(self, stream, durable):
        self.deleted = (stream, durable)

    async def pull_subscribe(self, subject, **kwargs):
        self.pull_kwargs = (subject, kwargs)
        return "pull-subscription"


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


async def test_core_subscription_broadcasts_to_every_api_instance():
    """出站推送不能使用共享 durable 竞争消费，否则目标 WS 可能在另一个实例。"""
    first = _FakeNatsConnection()
    second = _FakeNatsConnection()

    async def callback(msg):
        return None

    await nats_core.subscribe_core(first, nats_core.OUTBOUND_SUBJECT, callback)
    await nats_core.subscribe_core(second, nats_core.OUTBOUND_SUBJECT, callback)

    assert first.subscriptions == [(nats_core.OUTBOUND_SUBJECT, callback)]
    assert second.subscriptions == [(nats_core.OUTBOUND_SUBJECT, callback)]


async def test_pull_subscription_migrates_legacy_push_consumer_without_replay():
    js = _FakeMigratingJetStream()
    subscription = await nats_core.pull_subscribe(js, "im.inbound", "worker")

    assert subscription == "pull-subscription"
    assert js.deleted == ("EVENTS", "worker")
    config = js.pull_kwargs[1]["config"]
    assert config.opt_start_seq == 43
    assert config.deliver_subject is None


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


def test_persisted_reply_after_live_stream_only_emits_end_marker():
    envelope = {
        "event_id": "e1", "trace_id": "t1", "tenant_id": "tenant-a",
        "payload": {
            "message_id": "reply-1", "conversation_id": "conversation-1",
            "content": "已经实时推送", "in_reply_to": "input-1", "streamed": True,
        },
    }
    events = build_reply_events(envelope)
    assert [event["type"] for event in events] == ["reply.end"]
    assert events[0]["payload"]["content"] == "已经实时推送"
