"""100 句真实语料的一级意图命中对照（两级意图白名单版）。

语料来自《教育平台高并发 AI 客服机器人》Word 描述的 12 类场景。
对每条语料，规则命中 = 一级意图 == 期望；规则未命中（unknown）交 LLM 兜底，
属于两级架构的正常路径，不计为错误；只有「路由到错误的非 unknown 意图」才算错误。
"""

import json
from pathlib import Path

import pytest

from app.services.intent_service import classify_intent

CORPUS_PATH = Path(__file__).resolve().parents[2] / "evaluation" / "intent-corpus.json"


def _load_corpus():
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "sample",
    _load_corpus(),
    ids=[sample["id"] for sample in _load_corpus()],
)
def test_no_wrong_routing(sample):
    """每条语料要么命中期望的一级意图，要么 abstain 交 LLM 兜底，绝不误路由。"""
    actual = classify_intent(sample["input"]).intent
    assert actual in {sample["expected_intent"], "unknown"}, (
        f"{sample['id']}「{sample['input']}」期望 {sample['expected_intent']}，实际 {actual}"
    )


def test_rule_hit_rate_floor():
    """规则命中率应保持在高水位（其余交由 LLM 两级兜底，不要求 100% 规则命中）。"""
    corpus = _load_corpus()
    hits = sum(
        1 for sample in corpus if classify_intent(sample["input"]).intent == sample["expected_intent"]
    )
    rate = hits / len(corpus)
    assert rate >= 0.90, f"规则命中率 {rate:.2%} 低于 90%，词库覆盖退化"
