import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest_asyncio
from redis.asyncio import from_url

from app.core.config import settings
from app.core.database import async_session, engine
from app.core.security import create_access_token
from app.middleware.rate_limit import allowed
from app.models import Message
from app.schemas.intent import IntentResult
from app.services import assistant_service
from app.services.context_service import ConversationContextStore
from app.services.llm_gate import LLMRequestGate


@pytest_asyncio.fixture
async def real_redis():
    client = from_url(
        settings.redis_url,
        decode_responses=True,
        max_connections=settings.redis_max_connections,
    )
    await client.ping()
    yield client
    keys = []
    async for key in client.scan_iter(match="test:*"):
        keys.append(key)
    if keys:
        await client.delete(*keys)
    await client.aclose()


class LiveContextStore(ConversationContextStore):
    @staticmethod
    def _key(tenant_id, conversation_id) -> str:
        return f"test:context:{tenant_id}:{conversation_id}"


async def test_real_redis_context_window_ttl_and_tenant_isolation(real_redis):
    store = LiveContextStore(real_redis, max_messages=2, ttl_seconds=120)
    conversation_id = str(uuid.uuid4())

    for index in range(3):
        assert await store.append(
            "tenant-a",
            conversation_id,
            {"message_id": str(index), "role": "user", "content": str(index)},
        )
    assert await store.append(
        "tenant-b",
        conversation_id,
        {"message_id": "b", "role": "user", "content": "tenant-b"},
    )

    assert [item["message_id"] for item in await store.read("tenant-a", conversation_id)] == ["1", "2"]
    assert [item["message_id"] for item in await store.read("tenant-b", conversation_id)] == ["b"]
    ttl = await real_redis.ttl(store._key("tenant-a", conversation_id))
    assert 0 < ttl <= 120


async def test_postgres_fallback_backfills_real_redis_and_feeds_llm(
    real_redis, make_user, make_conversation, monkeypatch
):
    user = await make_user()
    conversation_id = await make_conversation(user["tenant_id"], user["user_id"])
    tenant_uuid = uuid.UUID(user["tenant_id"])
    conversation_uuid = uuid.UUID(conversation_id)
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        session.add_all(
            [
                Message(
                    message_id="first",
                    tenant_id=tenant_uuid,
                    conversation_id=conversation_uuid,
                    role="user",
                    content="我明天下午三点有英语课。",
                    status="completed",
                    trace_id="test",
                    created_at=now,
                ),
                Message(
                    message_id="reply-first",
                    tenant_id=tenant_uuid,
                    conversation_id=conversation_uuid,
                    role="assistant",
                    content="好的。",
                    status="completed",
                    trace_id="test",
                    created_at=now + timedelta(seconds=1),
                ),
                Message(
                    message_id="current",
                    tenant_id=tenant_uuid,
                    conversation_id=conversation_uuid,
                    role="user",
                    content="那提前半小时提醒我。",
                    status="processing",
                    trace_id="test",
                    created_at=now + timedelta(seconds=2),
                ),
            ]
        )
        await session.commit()

    store = LiveContextStore(real_redis, max_messages=20, ttl_seconds=120)
    await real_redis.delete(store._key(user["tenant_id"], conversation_id))
    llm = AsyncMock()
    llm.generate.return_value = "没问题。"
    monkeypatch.setattr(assistant_service, "llm_client", llm)
    monkeypatch.setattr(
        assistant_service,
        "classify_intent",
        lambda _: IntentResult(intent="chitchat", confidence=1, source="rule"),
    )

    async with async_session() as session:
        reply = await assistant_service.generate_reply(
            session,
            {
                "message_id": "current",
                "content": "那提前半小时提醒我。",
                "tenant_id": user["tenant_id"],
                "user_id": user["user_id"],
                "conversation_id": conversation_id,
            },
            context_store=store,
        )

    assert reply == "没问题。"
    llm.generate.assert_awaited_once_with(
        [
            {"role": "user", "content": "我明天下午三点有英语课。"},
            {"role": "assistant", "content": "好的。"},
            {"role": "user", "content": "那提前半小时提醒我。"},
        ]
    )
    cached = await store.read(user["tenant_id"], conversation_id)
    assert [item["message_id"] for item in cached] == ["first", "reply-first"]
    assert 0 < await real_redis.ttl(store._key(user["tenant_id"], conversation_id)) <= 120


async def test_redis_connection_error_uses_real_postgres_history(
    make_user, make_conversation
):
    user = await make_user()
    conversation_id = await make_conversation(user["tenant_id"], user["user_id"])
    async with async_session() as session:
        session.add(
            Message(
                message_id="persisted",
                tenant_id=uuid.UUID(user["tenant_id"]),
                conversation_id=uuid.UUID(conversation_id),
                role="user",
                content="数据库中的历史",
                status="completed",
                trace_id="test",
            )
        )
        await session.commit()

    unavailable_redis = from_url(
        "redis://127.0.0.1:1/0",
        decode_responses=True,
        socket_connect_timeout=0.2,
        socket_timeout=0.2,
    )
    store = ConversationContextStore(
        unavailable_redis, max_messages=20, ttl_seconds=120
    )
    try:
        async with async_session() as session:
            history = await store.get_for_llm(
                session, user["tenant_id"], conversation_id
            )
    finally:
        await unavailable_redis.aclose()

    assert history == [
        {
            "message_id": "persisted",
            "role": "user",
            "content": "数据库中的历史",
        }
    ]


async def test_real_redis_atomic_rate_limit_counts_concurrently(real_redis):
    tenant_id = f"test:{uuid.uuid4()}"
    user_id = str(uuid.uuid4())

    results = await asyncio.gather(
        *(allowed(real_redis, tenant_id, user_id, 100, 100) for _ in range(20))
    )

    assert all(results)
    tenant_keys = [key async for key in real_redis.scan_iter(match=f"rate:tenant:{tenant_id}:*")]
    user_keys = [key async for key in real_redis.scan_iter(match=f"rate:user:{tenant_id}:{user_id}:*")]
    assert len(tenant_keys) == len(user_keys) == 1
    assert await real_redis.get(tenant_keys[0]) == "20"
    assert await real_redis.get(user_keys[0]) == "20"
    assert await real_redis.ttl(tenant_keys[0]) > 0
    assert await real_redis.ttl(user_keys[0]) > 0
    await real_redis.delete(*tenant_keys, *user_keys)


async def test_fastapi_middleware_uses_real_redis_and_returns_429(
    real_redis, make_user, make_conversation, monkeypatch
):
    from app.main import app

    user = await make_user()
    first_conversation = await make_conversation(user["tenant_id"], user["user_id"])
    second_conversation = await make_conversation(user["tenant_id"], user["user_id"])
    token = create_access_token(
        tenant_id=user["tenant_id"], user_id=user["user_id"], role=user["role"]
    )
    app.state.redis = real_redis
    monkeypatch.setattr(settings, "tenant_rate_limit_per_minute", 10)
    monkeypatch.setattr(settings, "user_rate_limit_per_minute", 1)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get(
            f"/conversations/{first_conversation}/messages",
            headers={"Authorization": f"Bearer {token}"},
        )
        second = await client.get(
            f"/conversations/{second_conversation}/messages",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert first.status_code == 200
    assert second.status_code == 429
    keys = [
        key
        async for key in real_redis.scan_iter(
            match=f"rate:user:{user['tenant_id']}:{user['user_id']}:*"
        )
    ]
    assert len(keys) == 1
    assert await real_redis.get(keys[0]) == "2"
    assert await real_redis.ttl(keys[0]) > 0
    tenant_keys = [
        key
        async for key in real_redis.scan_iter(
            match=f"rate:tenant:{user['tenant_id']}:*"
        )
    ]
    await real_redis.delete(*keys, *tenant_keys)


async def test_health_ready_really_pings_redis(real_redis):
    from app.main import app

    app.state.engine = engine
    app.state.redis = real_redis
    app.state.settings = settings
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        live = await client.get("/health/live")
        ready = await client.get("/health/ready")

    assert live.status_code == 200
    assert ready.status_code == 200
    assert ready.json()["checks"]["redis"] == "ok"


async def test_llm_gate_waits_for_reply_and_global_rate_window(real_redis):
    await real_redis.delete("llm:gate:inflight", "llm:gate:starts")
    gate = LLMRequestGate(real_redis, max_inflight=1, starts_per_second=20, poll_seconds=0.01)
    first = await gate.acquire()
    second_task = asyncio.create_task(gate.acquire())
    await asyncio.sleep(0.05)
    assert not second_task.done()
    await gate.release(first)
    second = await asyncio.wait_for(second_task, timeout=0.5)
    await gate.release(second)

    await real_redis.delete("llm:gate:inflight", "llm:gate:starts")
    rate_gate = LLMRequestGate(real_redis, max_inflight=600, starts_per_second=1, poll_seconds=0.01)
    first = await rate_gate.acquire()
    await rate_gate.release(first)
    started = time.monotonic()
    second = await rate_gate.acquire()
    elapsed = time.monotonic() - started
    await rate_gate.release(second)
    assert elapsed >= 0.9
