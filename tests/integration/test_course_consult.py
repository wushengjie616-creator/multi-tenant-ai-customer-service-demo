"""课程咨询技能集成测试：真实 Redis 状态 + 真实 PG 图谱属性驱动推荐 + 租户隔离。"""

import uuid

import pytest_asyncio
from redis.asyncio import from_url

from app.core.config import settings
from app.core.database import async_session
from app.services import course_consult_service as svc
from app.services.graph_store import graph_store

CATALOG = """# 2026 年秋季课程

## 数学思维启蒙班 M1

科目：数学；适合学前至一年级，无需任何基础；目标：兴趣启蒙。12 次课程学费 1980 元。

## 数学思维同步班 B2

科目：数学；适合一至二年级，无需奥数基础；目标：巩固提分。14 次课程学费 2380 元。

## 奥数竞赛冲刺班 C1

科目：数学；适合四至六年级，需奥数基础；目标：竞赛冲刺。20 次课程学费 4280 元。
"""


@pytest_asyncio.fixture
async def live_redis(monkeypatch):
    client = from_url(
        settings.redis_url,
        decode_responses=True,
        max_connections=settings.redis_max_connections,
    )
    await client.ping()
    monkeypatch.setattr(svc, "redis_client", client)
    yield client
    keys = [k async for k in client.scan_iter(match="skill:course_consult:*")]
    if keys:
        await client.delete(*keys)
    await client.aclose()


async def test_skill_guides_turn_by_turn_then_recommends(make_user, live_redis):
    user = await make_user()
    tenant_id = user["tenant_id"]
    conversation_id = str(uuid.uuid4())
    async with async_session() as s:
        await graph_store.replace_document(
            s, tenant_id,
            document_id="course-catalog", title="2026 年秋季课程",
            source="xinghe://courses", content=CATALOG, version=1,
        )

    skill = svc.course_consult_skill
    payload = {"tenant_id": tenant_id, "conversation_id": conversation_id}

    async with async_session() as s:
        assert "年级" in await skill.handle(s, payload, "想给孩子报个班")
        assert "方向" in await skill.handle(s, payload, "四年级")
        assert "奥数" in await skill.handle(s, payload, "想学数学")
        assert "课外班" in await skill.handle(s, payload, "学过奥数")
        assert "目标" in await skill.handle(s, payload, "上过课外班")
        reply = await skill.handle(s, payload, "想冲竞赛")

    # 四年级 + 数学 + 有奥数基础 + 上过课外班 + 竞赛 → 只有 C1 命中。
    assert "奥数竞赛冲刺班 C1" in reply
    assert await skill.is_active(tenant_id, conversation_id) is False


async def test_skill_recommends_nothing_for_tenant_without_graph(make_user, live_redis):
    tenant_a = await make_user()
    tenant_b = await make_user()
    async with async_session() as s:
        await graph_store.replace_document(
            s, tenant_a["tenant_id"],
            document_id="d", title="t", source="s", content=CATALOG, version=1,
        )

    payload_b = {"tenant_id": tenant_b["tenant_id"], "conversation_id": str(uuid.uuid4())}
    async with async_session() as s:
        reply = await svc.course_consult_skill.handle(
            s, payload_b, "孩子四年级，学数学，学过奥数，上过课外班，想冲竞赛"
        )

    assert "暂时没有完全匹配" in reply  # 租户 B 查不到租户 A 的班型
