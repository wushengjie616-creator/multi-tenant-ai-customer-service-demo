import json
from types import SimpleNamespace

from app.services.context_service import ConversationContextStore


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.commands = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def delete(self, key):
        self.commands.append(("delete", key))
        return self

    def rpush(self, key, *values):
        self.commands.append(("rpush", key, values))
        return self

    def ltrim(self, key, start, end):
        self.commands.append(("ltrim", key, start, end))
        return self

    def expire(self, key, seconds):
        self.commands.append(("expire", key, seconds))
        return self

    async def execute(self):
        self.redis.executions += 1
        for command in self.commands:
            name, key, *args = command
            if name == "delete":
                self.redis.values.pop(key, None)
                self.redis.ttls.pop(key, None)
            elif name == "rpush":
                self.redis.values.setdefault(key, []).extend(args[0])
            elif name == "ltrim":
                start, end = args
                values = self.redis.values.get(key, [])
                start = len(values) + start if start < 0 else start
                end = len(values) + end if end < 0 else end
                self.redis.values[key] = values[max(start, 0) : end + 1]
            elif name == "expire":
                self.redis.ttls[key] = args[0]
                self.redis.expire_calls[key] = self.redis.expire_calls.get(key, 0) + 1
        return [True] * len(self.commands)


class FakeRedis:
    def __init__(self, *, broken=False):
        self.values = {}
        self.ttls = {}
        self.expire_calls = {}
        self.executions = 0
        self.broken = broken

    def pipeline(self, transaction=True):
        assert transaction is True
        if self.broken:
            raise ConnectionError("redis unavailable")
        return FakePipeline(self)

    async def lrange(self, key, start, end):
        if self.broken:
            raise ConnectionError("redis unavailable")
        values = self.values.get(key, [])
        return values[start:] if end == -1 else values[start : end + 1]


class FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeSession:
    def __init__(self, rows, summary=None):
        self.rows = rows
        self.summary = summary
        self.calls = 0

    async def scalar(self, statement):
        self.calls += 1
        return self.summary

    async def scalars(self, statement):
        self.calls += 1
        return FakeScalarResult(self.rows)


def message(message_id, role, content):
    return SimpleNamespace(message_id=message_id, role=role, content=content)


async def test_context_append_read_trim_ttl_refresh_and_tenant_isolation():
    redis = FakeRedis()
    store = ConversationContextStore(redis, max_messages=2, ttl_seconds=3600)

    assert await store.append("tenant-a", "shared-conversation", {"message_id": "1", "role": "user", "content": "一"})
    assert await store.append("tenant-a", "shared-conversation", {"message_id": "2", "role": "assistant", "content": "二"})
    assert await store.append("tenant-a", "shared-conversation", {"message_id": "3", "role": "user", "content": "三"})
    assert await store.append("tenant-b", "shared-conversation", {"message_id": "b1", "role": "user", "content": "租户 B"})

    assert await store.read("tenant-a", "shared-conversation") == [
        {"message_id": "2", "role": "assistant", "content": "二"},
        {"message_id": "3", "role": "user", "content": "三"},
    ]
    assert await store.read("tenant-b", "shared-conversation") == [
        {"message_id": "b1", "role": "user", "content": "租户 B"}
    ]
    key = "context:tenant-a:shared-conversation"
    assert redis.ttls[key] == 3600
    assert redis.expire_calls[key] == 3


async def test_redis_miss_loads_postgres_in_time_order_and_backfills():
    redis = FakeRedis()
    store = ConversationContextStore(redis, max_messages=3, ttl_seconds=3600)
    # PostgreSQL 查询应按时间倒序取最近窗口；service 返回给 LLM 前恢复为正序。
    session = FakeSession(
        [
            message("current", "user", "那提前半小时提醒我。"),
            message("reply-1", "assistant", "好的。"),
            message("first", "user", "我明天下午三点有英语课。"),
        ]
    )

    result = await store.get_for_llm(
        session,
        "tenant-a",
        "conversation-a",
        current_message_id="current",
    )

    assert result == [
        {"message_id": "first", "role": "user", "content": "我明天下午三点有英语课。"},
        {"message_id": "reply-1", "role": "assistant", "content": "好的。"},
    ]
    assert await store.read("tenant-a", "conversation-a") == result
    assert session.calls == 2


async def test_redis_error_uses_postgres_without_crashing_or_retrying():
    store = ConversationContextStore(FakeRedis(broken=True), max_messages=20, ttl_seconds=3600)
    session = FakeSession([message("first", "user", "历史消息")])

    result = await store.get_for_llm(session, "tenant-a", "conversation-a")

    assert result == [
        {"message_id": "first", "role": "user", "content": "历史消息"}
    ]
    assert session.calls == 2


async def test_redis_recovery_rebuilds_a_context_that_became_stale_during_outage():
    redis = FakeRedis(broken=True)
    store = ConversationContextStore(redis, max_messages=20, ttl_seconds=3600)
    session = FakeSession([message("first", "user", "故障前消息")])

    assert await store.get_for_llm(session, "tenant-a", "conversation-a")

    redis.broken = False
    key = "context:tenant-a:conversation-a"
    redis.values[key] = [
        json.dumps(
            {"message_id": "first", "role": "user", "content": "故障前消息"},
            ensure_ascii=False,
        )
    ]
    # Redis 故障期间 PostgreSQL 又可靠保存了一条消息，但旧热副本不知道它。
    session.rows = [
        message("during-outage", "assistant", "故障期间保存的消息"),
        message("first", "user", "故障前消息"),
    ]

    assert await store.get_for_llm(session, "tenant-a", "conversation-a") == [
        {"message_id": "first", "role": "user", "content": "故障前消息"},
        {
            "message_id": "during-outage",
            "role": "assistant",
            "content": "故障期间保存的消息",
        },
    ]
    assert session.calls == 4
    assert await store.read("tenant-a", "conversation-a") == [
        {"message_id": "first", "role": "user", "content": "故障前消息"},
        {
            "message_id": "during-outage",
            "role": "assistant",
            "content": "故障期间保存的消息",
        },
    ]


async def test_redis_hit_only_queries_persisted_summary():
    redis = FakeRedis()
    store = ConversationContextStore(redis, max_messages=20, ttl_seconds=3600)
    await store.append("tenant-a", "conversation-a", {"message_id": "cached", "role": "user", "content": "缓存历史"})
    session = FakeSession([])

    assert await store.get_for_llm(session, "tenant-a", "conversation-a") == [
        {"message_id": "cached", "role": "user", "content": "缓存历史"}
    ]
    assert session.calls == 1


async def test_persisted_summary_is_prepended_to_hot_context():
    redis = FakeRedis()
    store = ConversationContextStore(redis, max_messages=20, ttl_seconds=3600)
    await store.append("tenant-a", "conversation-a", {"message_id": "cached", "role": "user", "content": "最近问题"})
    session = FakeSession([], summary="用户曾询问退费，客服已说明办理时间。")

    assert await store.get_for_llm(session, "tenant-a", "conversation-a") == [
        {"message_id": "conversation-summary", "role": "system", "content": "早期会话摘要：\n用户曾询问退费，客服已说明办理时间。"},
        {"message_id": "cached", "role": "user", "content": "最近问题"},
    ]
