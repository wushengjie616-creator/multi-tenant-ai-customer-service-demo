"""P3 高确定性规则路由与安全兜底。"""

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
        ("请问课程退费政策是什么", "knowledge_qa"),
        ("你好呀", "chitchat"),
    ],
)
def test_rule_intents(text, expected):
    result = classify_intent(text)
    assert result.intent == expected
    assert result.source == "rule"
    assert result.confidence >= 0.9


@pytest.mark.parametrize("text", ["人工智能课程多少钱", "如何计算人工成本"])
def test_human_negative_phrases_do_not_trigger_handoff(text):
    assert classify_intent(text).intent != "human_handoff"


def test_unknown_text_abstains_instead_of_guessing_tool():
    result = classify_intent("蓝色的风在唱歌")
    assert result.intent == "unknown"
    assert result.source == "abstain"
    assert result.abstain_reason == "no_rule_match"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("校区地址在哪里？", "location_contact"),
        ("怎么报名体验课？", "enrollment"),
        ("外教老师有什么资质？", "teacher_info"),
        ("英语课程怎么收费？", "pricing"),
        ("最近有什么优惠？", "promotion"),
        ("教材和学习资料如何领取？", "material_info"),
    ],
)
def test_common_customer_service_domains_have_explicit_intents(text, expected):
    assert classify_intent(text).intent == expected


async def test_llm_classifier_failure_degrades_to_unknown_for_rag_fallback():
    llm = AsyncMock()
    llm.generate.side_effect = TimeoutError("provider timeout")

    result = await classify_intent_with_llm("孩子明天来不了", llm)

    assert result.intent == "unknown"
    assert result.source == "abstain"
    assert result.abstain_reason == "llm_classification_failed"
