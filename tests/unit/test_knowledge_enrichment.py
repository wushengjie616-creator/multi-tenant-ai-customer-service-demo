from unittest.mock import AsyncMock

from app.services import knowledge_ingestion


async def test_llm_enrichment_failure_falls_back_without_blocking_import():
    build_enriched = getattr(
        knowledge_ingestion, "build_enriched_document_chunks", None
    )
    assert callable(build_enriched), "knowledge ingestion must expose async enrichment"
    llm = AsyncMock()
    llm.generate.side_effect = TimeoutError("provider unavailable")

    chunks = await build_enriched(
        tenant_id="tenant-a",
        document_id="trial-policy",
        title="试听课安排",
        source="demo://trial",
        content="# 周末试听\n\n体验课需提前两天预约，每周三晚上七点开课。",
        version=1,
        llm=llm,
    )

    assert chunks
    assert all(chunk["payload"]["enrichment_status"] == "fallback" for chunk in chunks)
    assert "试听课安排" in chunks[0]["payload"]["entities"]
    assert "体验课" in chunks[0]["payload"]["search_text"]


async def test_valid_llm_enrichment_is_merged_with_deterministic_metadata():
    llm = AsyncMock()
    llm.generate.return_value = """```json
    {"entities":["示例校区","周三口语课"],
     "aliases":["示例教学点"],
     "topics":["上课安排"],
     "keywords":["预约"],
     "summary":"介绍周三口语课的上课地点。",
     "suggested_questions":["周三口语课在哪里上课？","示例校区有哪些课程？"]}
    ```"""

    chunks = await knowledge_ingestion.build_enriched_document_chunks(
        tenant_id="tenant-a",
        document_id="schedule",
        title="校区课表",
        source="demo://schedule",
        content="周三口语课在示例校区上课。",
        version=1,
        llm=llm,
    )

    payload = chunks[0]["payload"]
    assert payload["enrichment_status"] == "llm"
    assert "示例校区" in payload["entities"]
    assert "示例教学点" in payload["aliases"]
    assert "上课安排" in payload["search_text"]
    assert payload["summary"] == "介绍周三口语课的上课地点。"
    assert payload["suggested_questions"] == [
        "周三口语课在哪里上课？",
        "示例校区有哪些课程？",
    ]
    assert "周三口语课在哪里上课" in payload["search_text"]


async def test_unknown_query_uses_llm_entities_with_rule_fallback_available():
    extractor = getattr(knowledge_ingestion, "extract_query_entities", None)
    assert callable(extractor), "unknown queries must expose entity extraction"
    llm = AsyncMock()
    llm.generate.return_value = (
        '{"entities":["星河屋"],"aliases":["银河自习室"],'
        '"topics":["开放时间"],"keywords":["周五"]}'
    )

    entities = await extractor("星河屋啥时候开放？", llm)

    assert "星河屋" in entities
    assert "银河自习室" in entities
    assert "开放时间" in entities


async def test_query_expansion_adds_semantic_course_synonyms():
    llm = AsyncMock()
    llm.generate.return_value = (
        '{"queries":["有哪些课程","课程体系"],'
        '"entities":["班级"],"concepts":["课程级别","分班"]}'
    )

    terms = await knowledge_ingestion.extract_query_entities("有哪些班级？", llm)

    assert "班级" in terms
    assert "课程体系" in terms
    assert "课程级别" in terms
    assert "课程体系" in terms
    assert "课程级别" in terms


def test_fallback_enrichment_normalizes_numbered_headings_for_exact_recall():
    metadata = knowledge_ingestion.fallback_enrichment(
        "英语课程政策",
        "# 英语课程政策\n\n## 1. 课程体系\n\n启蒙英语适合 4-6 岁。",
    )

    assert "课程体系" in metadata["topics"]
    assert "1. 课程体系" not in metadata["topics"]


async def test_query_expansion_falls_back_to_deterministic_class_synonyms():
    llm = AsyncMock()
    llm.generate.side_effect = TimeoutError("provider unavailable")

    terms = await knowledge_ingestion.extract_query_entities("有哪些班级？", llm)

    assert "班级" in terms


def test_fallback_enrichment_keeps_course_codes_and_catalog_intent():
    document = knowledge_ingestion.fallback_enrichment(
        "2026 秋季课程与班级",
        "## 数学思维进阶班 A3\n每周四 18:30\n## 科学探究实验班 S2\n每周六 10:00",
    )
    query = knowledge_ingestion.fallback_enrichment("", "你们有哪些班？")
    schedule = knowledge_ingestion.fallback_enrichment("", "数学 A3 什么时候上课？")

    assert {"A3", "S2", "课程目录"}.issubset(set(document["entities"] + document["aliases"]))
    assert "课程目录" in query["aliases"]
    assert "A3" in schedule["entities"]


def test_fallback_enrichment_keeps_bare_codes_when_catalog_is_large():
    # 六班课程目录的 headings/time_amounts 会超过 `_unique` 的 limit=20；课程编码是
    # 最高信号实体，必须排在前面，不能被截断挤出（否则「A3」查询检索不到课程目录）。
    catalog = (
        "# 2026 年秋季课程\n\n"
        + "".join(
            f"## 班型{i} {code}\n\n科目：数学；每周四 18:30–20:00，静安校区。12 次学费 2000 元。\n\n"
            for i, code in enumerate(["M1", "B2", "A3", "S2", "P1", "C1"])
        )
    )
    metadata = knowledge_ingestion.fallback_enrichment("2026 秋季课程与班级", catalog)
    assert {"M1", "B2", "A3", "S2", "P1", "C1"}.issubset(set(metadata["entities"]))
    assert "A3" in metadata["keywords"]


def test_fallback_enrichment_marks_schedule_content_with_class_time_alias():
    # 含「每周X HH:MM」课表句式的文档应能命中「什么时候上课」这类口语问法。
    schedule_doc = knowledge_ingestion.fallback_enrichment(
        "2026 秋季课程与班级", "## 数学思维进阶班 A3\n每周四 18:30–20:00"
    )
    non_schedule = knowledge_ingestion.fallback_enrichment(
        "学费与自动续费", "数学思维进阶班 A3，实付 3280 元"
    )
    assert "上课时间" in schedule_doc["aliases"]
    assert "上课时间" not in non_schedule["aliases"]
