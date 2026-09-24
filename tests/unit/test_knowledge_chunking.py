from app.services.knowledge_ingestion import build_document_chunks, split_markdown


def test_split_markdown_preserves_headings_and_bounds_chunk_size():
    content = "# 退费\n\n" + ("申请材料包括订单号和付款凭证。" * 20) + "\n\n## 时限\n\n审核通常需要五个工作日。"

    chunks = split_markdown(content, max_chars=120)

    assert len(chunks) >= 3
    assert all(1 <= len(chunk) <= 120 for chunk in chunks)
    assert chunks[0].startswith("# 退费")
    assert any("## 时限" in chunk for chunk in chunks)
    assert "".join(chunk.replace("\n\n", "") for chunk in chunks).replace("\n", "")


def test_document_chunks_have_stable_ids_and_governance_payload():
    kwargs = {
        "tenant_id": "tenant-a",
        "document_id": "refund-policy",
        "title": "退费政策",
        "source": "demo://refund",
        "content": "# 申请\n\n请提交订单号。\n\n# 时限\n\n五个工作日内审核。",
        "version": 2,
        "effective_from": "2026-01-01T00:00:00+08:00",
        "effective_until": None,
        "max_chars": 20,
    }

    first = build_document_chunks(**kwargs)
    second = build_document_chunks(**kwargs)

    assert [item["point_id"] for item in first] == [item["point_id"] for item in second]
    assert len({item["chunk_id"] for item in first}) == len(first)
    assert [item["payload"]["chunk_index"] for item in first] == list(range(len(first)))
    assert all(item["payload"]["effective_from"] == kwargs["effective_from"] for item in first)
    assert all(item["payload"]["review_status"] == "approved" for item in first)
    assert all("entities" in item["payload"] for item in first)
    assert all("aliases" in item["payload"] for item in first)
    assert all("topics" in item["payload"] for item in first)
    assert all("keywords" in item["payload"] for item in first)
    assert all("search_text" in item["payload"] for item in first)
    assert all(item["payload"]["enrichment_status"] == "fallback" for item in first)
    assert "退费政策" in first[0]["payload"]["entities"]
    assert first[0]["payload"]["document_content"] == kwargs["content"]
    assert all("document_content" not in item["payload"] for item in first[1:])
