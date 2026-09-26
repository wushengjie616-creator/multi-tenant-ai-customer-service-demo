"""P3 高确定性规则路由与安全兜底（两级意图白名单版）。"""

import pytest
from unittest.mock import AsyncMock

from app.services.intent_service import classify_intent, classify_intent_with_llm


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("我要转人工客服", "human_handoff"),
        ("帮我查询发票", "finance"),
        ("明天九点提醒我上课", "schedule"),
        ("请帮我关闭自动续费", "platform_command"),
        ("请问课程退费政策是什么", "knowledge"),
        ("你好呀", "chitchat"),
    ],
)
def test_rule_intents(text, expected):
    result = classify_intent(text)
    assert result.intent == expected
    assert result.source == "rule"
    assert result.confidence >= 0.9


def test_rule_hit_does_not_produce_secondary_intent():
    """规则命中时二级意图保持 None，只有 LLM 兜底才生产。"""
    assert classify_intent("请问课程退费政策是什么").secondary_intent is None
    assert classify_intent("明天九点提醒我上课").secondary_intent is None


@pytest.mark.parametrize("text", ["人工智能课程多少钱", "如何计算人工成本"])
def test_human_negative_phrases_do_not_trigger_handoff(text):
    assert classify_intent(text).intent != "human_handoff"


def test_identity_question_is_chitchat_not_handoff():
    assert classify_intent("你是真人还是机器人").intent == "chitchat"


def test_unknown_text_abstains_instead_of_guessing_tool():
    result = classify_intent("蓝色的风在唱歌")
    assert result.intent == "unknown"
    assert result.source == "abstain"
    assert result.abstain_reason == "no_rule_match"


@pytest.mark.parametrize("text", ["请假规则是什么？", "缺课和补课规则怎么规定？"])
def test_leave_policy_questions_are_knowledge_not_write_commands(text):
    assert classify_intent(text).intent == "knowledge"


@pytest.mark.parametrize(
    "text",
    [
        "校区地址在哪里？",
        "怎么报名体验课？",
        "外教老师有什么资质？",
        "英语课程怎么收费？",
        "最近有什么优惠？",
        "教材和学习资料如何领取？",
    ],
)
def test_common_customer_service_domains_collapse_to_knowledge(text):
    assert classify_intent(text).intent == "knowledge"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("退费政策是什么？", "knowledge"),
        ("退费进度怎么样？", "finance"),
        ("退款到账了吗？", "finance"),
        ("上课提醒怎么设置？", "schedule"),
        ("提交请假申请", "platform_command"),
        ("请假规则是什么？", "knowledge"),
    ],
)
def test_disambiguation_rules(text, expected):
    assert classify_intent(text).intent == expected


async def test_llm_classifier_failure_degrades_to_unknown_for_rag_fallback():
    llm = AsyncMock()
    llm.generate.side_effect = TimeoutError("provider timeout")

    result = await classify_intent_with_llm("孩子明天来不了", llm)

    assert result.intent == "unknown"
    assert result.source == "abstain"
    assert result.abstain_reason == "llm_classification_failed"


async def test_llm_fallback_produces_whitelisted_intent_and_secondary_intent():
    llm = AsyncMock()
    llm.generate.return_value = (
        '{"intent":"knowledge","secondary_intent":"查询请假规则","confidence":0.92,"parameters":{}}'
    )

    result = await classify_intent_with_llm("孩子请假有什么规定", llm)

    assert result.intent == "knowledge"
    assert result.source == "llm"
    assert result.secondary_intent == "查询请假规则"


async def test_llm_fallback_rejects_out_of_whitelist_intent():
    llm = AsyncMock()
    llm.generate.return_value = (
        '{"intent":"delete_user","secondary_intent":"删除用户","confidence":0.99,"parameters":{}}'
    )

    result = await classify_intent_with_llm("帮我删掉一个用户", llm)

    assert result.intent == "unknown"
    assert result.source == "abstain"


@pytest.mark.parametrize(
    "text",
    [
        "我家孩子8岁适合什么课程",
        "我家孩子没上过课外班适合什么班",
        "孩子三年级想学数学报什么班",
        "我家小孩有奥数基础想报班",
        "儿子上什么课比较合适",
    ],
)
def test_course_consultation_signals(text):
    result = classify_intent(text)
    assert result.intent == "course_consultation"
    assert result.source == "rule"
    assert result.confidence >= 0.9


@pytest.mark.parametrize(
    "text",
    [
        "有哪些课程",
        "课程多少钱",
        "数学思维班是怎么上课的",
        "孩子上课时间是什么时候",
        "请假规则是什么",
    ],
)
def test_general_course_inquiry_is_not_course_consultation(text):
    # 泛课程了解 / 价格 / 上课时间 / 请假规则仍归 knowledge，不做选课引导。
    assert classify_intent(text).intent != "course_consultation"
