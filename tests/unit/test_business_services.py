"""P4-P7 业务核心的确定性边界。"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.clients.knowledge_client import is_payload_active
from app.services import finance_service, handoff_service
from app.services.finance_service import safe_finance_result
from app.services.handoff_service import should_handoff
from app.services.rag_service import answer_question, build_grounded_answer, deterministic_embedding
from app.services.reminder_service import advance_occurrence, advance_to_future, compute_next_run


def test_rag_requires_supported_evidence_and_validates_citations():
    hits = [
        {
            "chunk_id": "c1",
            "score": 0.91,
            "content": "审核通过后，退款于 7-15 个工作日内原路退回。",
            "title": "退费政策",
            "source": "demo://refund",
        }
    ]
    answer = build_grounded_answer("退款多久？", hits, requested_citations=["c1", "forged"])
    assert answer["evidence_level"] == "SUPPORTED"
    assert answer["citations"] == [
        {"chunk_id": "c1", "title": "退费政策", "source": "demo://refund"}
    ]
    assert "7-15" in answer["answer"]


def test_rag_no_evidence_is_fixed_refusal():
    answer = build_grounded_answer("天气", [], requested_citations=[])
    assert answer["evidence_level"] == "NONE"
    assert "暂时没有查到明确依据" in answer["answer"]
    assert "转人工" not in answer["answer"]
    assert answer["citations"] == []
    assert len(deterministic_embedding("same")) == 64
    assert deterministic_embedding("same") == deterministic_embedding("same")


async def test_rag_reranks_vector_candidates_by_question_overlap():
    store = AsyncMock()
    store.search.return_value = [
        {
            "chunk_id": "faq:1:0",
            "score": 0.9,
            "content": "常见问题：如何申请退费？",
            "title": "常见问题",
            "source": "demo://faq",
        },
        {
            "chunk_id": "refund:1:0",
            "score": 0.1,
            "content": "开课 30 天后原则上不予退费，特殊情况可申请人工审核。",
            "title": "退费政策",
            "source": "demo://refund",
        },
    ]

    result = await answer_question(store, "tenant-a", "开课 30 天后还能退费吗？")

    assert result["citations"] == [
        {"chunk_id": "refund:1:0", "title": "退费政策", "source": "demo://refund"}
    ]
    assert "不予退费" in result["answer"]


async def test_rag_can_recover_a_vector_miss_through_entity_search():
    store = AsyncMock()
    store.search.return_value = []
    store.search_by_entities.return_value = [
        {
            "chunk_id": "trial:1:0",
            "score": 0.0,
            "content": "体验课需提前两天预约，每周三晚上七点开课。",
            "title": "试听课安排",
            "source": "demo://trial",
            "entities": ["试听课", "每周三", "晚上七点"],
            "aliases": ["体验课"],
            "topics": ["课程安排"],
            "keywords": ["预约", "开课时间"],
        }
    ]

    result = await answer_question(
        store,
        "tenant-a",
        "体验课周几几点开？",
        query_entities=["体验课", "开课时间"],
    )

    store.search_by_entities.assert_awaited_once_with(
        "tenant-a", ["体验课", "开课时间"], limit=16
    )
    assert result["evidence_level"] == "SUPPORTED"
    assert result["citations"][0]["title"] == "试听课安排"


async def test_rag_uses_full_text_when_entity_metadata_does_not_contain_term():
    store = AsyncMock()
    store.search.return_value = []
    store.search_by_entities.return_value = []
    store.search_by_text.return_value = [
        {
            "chunk_id": "room:1:0",
            "score": 0.0,
            "content": "特殊活动在银河教室举行。",
            "title": "活动地点",
            "source": "demo://room",
        }
    ]

    result = await answer_question(
        store,
        "tenant-a",
        "银河教室在哪里？",
        query_entities=["银河教室"],
    )

    store.search_by_text.assert_awaited_once_with(
        "tenant-a", ["银河教室"], limit=16
    )
    assert result["evidence_level"] == "SUPPORTED"
    assert result["citations"][0]["title"] == "活动地点"


async def test_rag_uses_tenant_scoped_semantic_rerank_when_lexical_recall_misses():
    store = AsyncMock()
    store.search.return_value = []
    store.search_by_entities.return_value = []
    store.search_by_text.return_value = []
    store.list_candidate_chunks.return_value = [
        {
            "chunk_id": "course:1:0",
            "score": 0.0,
            "content": "课程体系包括启蒙英语、少儿英语和进阶英语三个级别。",
            "title": "英语课程政策",
            "source": "demo://course",
        },
        {
            "chunk_id": "refund:1:0",
            "score": 0.0,
            "content": "开课前申请退费可全额退款。",
            "title": "退费政策",
            "source": "demo://refund",
        },
    ]
    llm = AsyncMock()
    llm.generate.return_value = '{"chunk_ids":["course:1:0"]}'

    result = await answer_question(
        store,
        "tenant-a",
        "有哪些班级？",
        query_entities=["班级", "课程体系"],
        semantic_reranker=llm,
    )

    store.list_candidate_chunks.assert_awaited_once_with("tenant-a", limit=40)
    assert result["evidence_level"] == "SUPPORTED"
    assert result["citations"][0]["title"] == "英语课程政策"
    assert "启蒙英语" in result["answer"]


async def test_semantic_rerank_cannot_invent_evidence_from_unrelated_document():
    store = AsyncMock()
    store.search.return_value = []
    store.search_by_entities.return_value = []
    store.search_by_text.return_value = []
    store.list_candidate_chunks.return_value = [
        {
            "chunk_id": "hobby:1:0",
            "score": 0.0,
            "content": "小吴喜欢学英语，但是他总是学不明白。",
            "title": "小吴的爱好",
            "source": "demo://hobby",
        }
    ]
    llm = AsyncMock()
    llm.generate.return_value = '{"chunk_ids":[]}'

    result = await answer_question(
        store,
        "tenant-a",
        "你们这里可以学什么？",
        query_entities=["课程"],
        semantic_reranker=llm,
    )

    assert result["evidence_level"] == "NONE"
    assert result["citations"] == []


async def test_rag_store_outage_returns_fixed_no_evidence_instead_of_500():
    store = AsyncMock()
    store.search.side_effect = RuntimeError("qdrant unavailable")

    result = await answer_question(
        store,
        "tenant-a",
        "现在有哪些课程？",
        query_entities=["课程"],
    )

    assert result == {
        "answer": "暂时没有查到明确依据，请换一种说法或补充更多细节。",
        "evidence_level": "NONE",
        "citations": [],
    }


def test_knowledge_effective_window_is_enforced():
    now = datetime(2026, 9, 23, tzinfo=timezone.utc)
    assert is_payload_active({"effective_from": None, "effective_until": None}, now)
    assert is_payload_active({"effective_from": "2026-01-01T00:00:00+00:00", "effective_until": "2027-01-01T00:00:00+00:00"}, now)
    assert not is_payload_active({"effective_from": "2026-10-01T00:00:00+00:00"}, now)
    assert not is_payload_active({"effective_until": "2026-09-23T00:00:00+00:00"}, now)
    assert not is_payload_active({"effective_from": "not-a-date"}, now)


def test_finance_masks_before_return_and_never_invents_on_failure():
    ok = safe_finance_result(
        {"order_id": "O-1", "amount": 1200, "email": "parent@example.com"}
    )
    assert ok["status"] == "ok"
    assert "parent@example.com" not in str(ok)
    assert "p***@example.com" in str(ok)

    failed = safe_finance_result(None, error="timeout")
    assert failed["status"] == "unavailable"
    assert "暂时无法查询" in failed["message"]
    assert "amount" not in failed


def test_finance_success_is_rendered_as_customer_language_not_raw_json():
    result = safe_finance_result(
        {"order_id": "EDU-1", "amount": 2399, "status": "issued", "email": "parent@example.com"}
    )
    reply = finance_service.format_finance_reply("invoice", result)
    assert "EDU-1" in reply
    assert "¥2,399" in reply
    assert "p***@example.com" in reply
    assert not reply.startswith("{")


@pytest.mark.parametrize(
    ("explicit", "count", "expected"),
    [(True, 0, True), (False, 1, False), (False, 2, True)],
)
def test_handoff_trigger(explicit, count, expected):
    assert should_handoff(explicit_request=explicit, dissatisfaction_count=count) is expected


@pytest.mark.parametrize("text", ["还是不对", "你这回答没用", "答非所问"])
def test_negative_feedback_is_recognized(text):
    assert handoff_service.is_dissatisfaction(text)


def test_regular_question_is_not_negative_feedback():
    assert not handoff_service.is_dissatisfaction("你们有哪些课程？")


def test_once_reminder_is_normalized_to_utc_and_rejects_past():
    now = datetime(2026, 9, 23, 0, 0, tzinfo=timezone.utc)
    result = compute_next_run(
        run_at_local="2026-09-24T09:00:00",
        timezone_name="Asia/Shanghai",
        repeat="once",
        now=now,
    )
    assert result.isoformat() == "2026-09-24T01:00:00+00:00"
    early = compute_next_run(
        run_at_local="2026-09-24T09:00:00",
        timezone_name="Asia/Shanghai",
        repeat="once",
        lead_time_minutes=30,
        now=now,
    )
    assert early.isoformat() == "2026-09-24T00:30:00+00:00"
    with pytest.raises(ValueError):
        compute_next_run(
            run_at_local="2026-09-22T09:00:00",
            timezone_name="Asia/Shanghai",
            repeat="once",
            now=now,
        )


def test_repeating_reminder_advances_and_skips_weekend():
    friday = datetime(2026, 9, 25, 1, 0, tzinfo=timezone.utc)
    assert advance_occurrence(friday, "daily") == datetime(2026, 9, 26, 1, 0, tzinfo=timezone.utc)
    assert advance_occurrence(friday, "weekly") == datetime(2026, 10, 2, 1, 0, tzinfo=timezone.utc)
    assert advance_occurrence(friday, "weekdays") == datetime(2026, 9, 28, 1, 0, tzinfo=timezone.utc)
    assert advance_occurrence(friday, "once") is None


def test_weekday_reminder_uses_tenant_local_calendar_near_utc_date_boundary():
    # 2026-09-24 17:00 UTC is Friday 01:00 in Shanghai. The next weekday is
    # Monday 01:00 local, not Saturday 01:00 local.
    friday_local = datetime(2026, 9, 24, 17, 0, tzinfo=timezone.utc)
    assert advance_occurrence(
        friday_local, "weekdays", timezone_name="Asia/Shanghai"
    ) == datetime(2026, 9, 27, 17, 0, tzinfo=timezone.utc)


def test_daily_reminder_preserves_wall_clock_across_dst_change():
    # New York switches from EDT to EST on 2026-11-01. 09:00 local therefore
    # moves from 13:00 UTC to 14:00 UTC while preserving the user's wall time.
    before_dst_end = datetime(2026, 10, 31, 13, 0, tzinfo=timezone.utc)
    assert advance_occurrence(
        before_dst_end, "daily", timezone_name="America/New_York"
    ) == datetime(2026, 11, 1, 14, 0, tzinfo=timezone.utc)


def test_repeating_reminder_skips_stale_occurrences_after_worker_downtime():
    stale = datetime(2026, 9, 22, 1, 0, tzinfo=timezone.utc)
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    assert advance_to_future(stale, "daily", now) == datetime(2026, 9, 26, 1, 0, tzinfo=timezone.utc)
