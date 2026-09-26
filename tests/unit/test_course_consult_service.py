"""课程咨询技能状态机单测：确定性槽位抽取 + LLM 技能兜底/合成 + Redis 状态推进 + 图谱驱动推荐。"""

import json
from unittest.mock import AsyncMock

import pytest

import app.services.course_consult_service as svc


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value
        return True

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def delete(self, key):
        self.store.pop(key, None)
        return 1


class FakeGraphStore:
    def __init__(self, classes):
        self.classes = classes

    async def list_classes(self, session, tenant_id):
        return self.classes


CATALOG = [
    {"name": "数学思维同步班 B2", "attributes": {"编码": "B2", "科目": "数学", "适合年级": "一至二年级", "需基础": "none", "目标": "提分", "价格": 2380}},
    {"name": "数学思维进阶班 A3", "attributes": {"编码": "A3", "科目": "数学", "适合年级": "三至四年级", "价格": 3280}},
]


@pytest.fixture
def fake_redis(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(svc, "redis_client", redis)
    return redis


@pytest.fixture
def fake_graph(monkeypatch):
    graph = FakeGraphStore(CATALOG)
    monkeypatch.setattr(svc, "graph_store", graph)
    return graph


def _payload():
    return {"tenant_id": "t1", "conversation_id": "c1"}


async def test_first_turn_asks_grade(fake_redis):
    # 首轮命中意图，但未带任何槽位信息 → 先问年级。
    reply = await svc.course_consult_skill.handle(None, _payload(), "适合报什么班")
    assert "年级" in reply


async def test_multi_turn_fills_slots_then_recommends(fake_redis, fake_graph):
    skill = svc.course_consult_skill
    payload = _payload()

    # 第 1 轮：家长一句话带出年级 + 兴趣。
    r1 = await skill.handle(None, payload, "孩子三年级，想学数学")
    assert "奥数" in r1  # 下一槽位是基础

    # 第 2 轮：回答无基础。
    r2 = await skill.handle(None, payload, "没有奥数基础")
    assert "课外班" in r2  # 下一槽位是是否上过课外班

    # 第 3 轮：没上过课外班。
    r3 = await skill.handle(None, payload, "没上过课外班")
    assert "目标" in r3  # 下一槽位是目标

    # 第 4 轮：目标是提分 → 槽位收满，进入推荐（session=None 降级）。
    r4 = await skill.handle(None, payload, "想提分")
    assert "无法查询课程库" in r4
    # 状态已清空，不再活跃。
    assert await skill.is_active("t1", "c1") is False


async def test_recommend_uses_graph_classes_when_session_available(fake_redis, fake_graph):
    skill = svc.course_consult_skill
    payload = _payload()
    # 一句话带全所有槽位：三年级 + 数学 + 无基础 + 没上过课外班 + 提分。
    reply = await skill.handle("fake-session", payload, "孩子三年级，学数学，没有奥数基础，没上过课外班，想提分")
    assert "数学思维进阶班 A3" in reply
    assert "A3" in reply
    assert await skill.is_active("t1", "c1") is False


async def test_exit_terms_clear_state(fake_redis, fake_graph):
    skill = svc.course_consult_skill
    payload = _payload()
    await skill.handle(None, payload, "孩子三年级")
    assert await skill.is_active("t1", "c1") is True
    reply = await skill.handle(None, payload, "算了不用了")
    assert await skill.is_active("t1", "c1") is False
    assert "随时" in reply


async def test_state_is_tenant_scoped(fake_redis, fake_graph):
    skill = svc.course_consult_skill
    await skill.handle(None, _payload(), "孩子三年级")
    assert await skill.is_active("t1", "c1") is True
    assert await skill.is_active("t2", "c1") is False  # 另一租户同名会话无状态


def test_slot_extractors_are_deterministic():
    assert svc._extract_grade("孩子现在三年级") == "三年级"
    assert svc._extract_grade("我家娃8岁") == "8岁"
    assert svc._extract_interest("想学编程") == "编程"
    assert svc._extract_foundation("没有奥数基础") == "无"
    assert svc._extract_foundation("学过奥数") == "有"
    assert svc._extract_has_taken("没上过课外班") == "否"
    assert svc._extract_goal("想提分") == "提分"
    assert svc._extract_goal("冲竞赛") == "竞赛"


# ── LLM 技能：语义抽取 + 校验 + 话术合成 ─────────────────────────────────


async def test_llm_skill_extraction_normalizes_and_validates():
    llm = AsyncMock()
    llm.generate.return_value = (
        '{"slots":{"grade":"三年级","interest":"数学","foundation":"无","goal":"奥数"}}'
    )

    slots = await svc._extract_slots_with_llm("孩子三年级想学数学", llm)

    assert slots["grade"] == "三年级"
    assert slots["interest"] == "数学"
    assert slots["foundation"] == "无"
    # 「奥数」不是合法目标枚举，被校验拒绝。
    assert "goal" not in slots


async def test_llm_skill_rejects_unparsable_grade():
    llm = AsyncMock()
    llm.generate.return_value = '{"slots":{"grade":"随便填的一个值"}}'

    slots = await svc._extract_slots_with_llm("孩子三年级", llm)

    # grade_to_level 解析不通 → 拒绝，避免脏值污染推荐。
    assert "grade" not in slots


async def test_llm_skill_phrases_recommendation_grounded_in_candidates():
    llm = AsyncMock()
    llm.generate.return_value = '{"reply":"为您推荐数学思维进阶班 A3，适合三至四年级。"}'
    result = {
        "candidates": [
            {"name": "数学思维进阶班 A3", "attributes": {"编码": "A3"}, "score": 5, "reasons": []}
        ]
    }

    reply = await svc._phrase_recommendation_with_llm(llm, result)

    assert "A3" in reply


async def test_handle_uses_llm_skill_for_semantic_gap(fake_redis):
    # 「信息学」不在确定性兴趣词表内，规则抽不到年级/兴趣；LLM 技能补位。
    llm = AsyncMock()
    llm.generate.return_value = '{"slots":{"grade":"三年级","interest":"编程"}}'

    reply = await svc.course_consult_skill.handle(
        None, _payload(), "孩子想搞信息学竞赛", tenant_llm=llm
    )

    assert "奥数" in reply  # 下一槽位是基础（年级/兴趣已补上，目标由规则命中）
    state = json.loads(fake_redis.store[svc._key("t1", "c1")])
    assert state["slots"]["grade"] == "三年级"
    assert state["slots"]["interest"] == "编程"


async def test_handle_phrases_recommendation_with_llm(fake_redis, fake_graph):
    llm = AsyncMock()
    llm.generate.return_value = '{"reply":"根据孩子情况，推荐数学思维进阶班 A3，可预约测评。"}'

    reply = await svc.course_consult_skill.handle(
        "fake-session", _payload(),
        "孩子三年级，学数学，没有奥数基础，没上过课外班，想提分",
        tenant_llm=llm,
    )

    assert "A3" in reply
    assert "mock" not in reply


async def test_handle_falls_back_when_llm_skill_fails(fake_redis, fake_graph):
    llm = AsyncMock()
    llm.generate.side_effect = TimeoutError("provider down")

    reply = await svc.course_consult_skill.handle(
        "fake-session", _payload(),
        "孩子三年级，学数学，没有奥数基础，没上过课外班，想提分",
        tenant_llm=llm,
    )

    # LLM 全链路失败 → 确定性模板兜底，仍给出推荐。
    assert "数学思维进阶班 A3" in reply
    assert await svc.course_consult_skill.is_active("t1", "c1") is False
