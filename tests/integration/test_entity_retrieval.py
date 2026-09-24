import uuid

from app.clients.knowledge_client import TenantScopedVectorStore
from app.services.knowledge_ingestion import build_document_chunks


async def test_entity_retrieval_is_tenant_scoped_against_real_qdrant():
    store = TenantScopedVectorStore()
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    document_id = "entity-isolation-test"
    try:
        for tenant_id, title in (
            (tenant_a, "A 租户试听课"),
            (tenant_b, "B 租户试听课"),
        ):
            chunks = build_document_chunks(
                tenant_id=tenant_id,
                document_id=document_id,
                title=title,
                source="test://entity",
                content="试听课需预约，也可称为体验课，在银河教室上课。",
                version=1,
            )
            await store.upsert_chunks(tenant_id, chunks)

        hits = await store.search_by_entities(tenant_a, ["体验课"], limit=10)

        assert hits
        assert {hit["tenant_id"] for hit in hits} == {tenant_a}
        assert all(hit["document_id"] == document_id for hit in hits)

        text_hits = await store.search_by_text(tenant_a, ["银河教室"], limit=10)
        assert text_hits
        assert {hit["tenant_id"] for hit in text_hits} == {tenant_a}
    finally:
        await store.delete_document(tenant_a, document_id)
        await store.delete_document(tenant_b, document_id)
        await store.client.close()


async def test_document_listing_reconstructs_tenant_scoped_uploads_from_qdrant():
    store = TenantScopedVectorStore()
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    document_id = "document-list-test"
    content = "第一行：课程介绍\n第二行：适合三年级\n第三行：周末上课\n第四行：请提前预约"
    try:
        await store.upsert_chunks(
            tenant_a,
            build_document_chunks(
                tenant_id=tenant_a,
                document_id=document_id,
                title="A 租户课程说明",
                source="ui://tenant-upload/document-list-test",
                content=content,
                version=1,
                max_chars=20,
            ),
        )
        await store.upsert_chunks(
            tenant_b,
            build_document_chunks(
                tenant_id=tenant_b,
                document_id=document_id,
                title="B 租户私有说明",
                source="ui://tenant-upload/document-list-test",
                content="不能被 A 租户看到",
                version=1,
            ),
        )

        documents = await store.list_documents(tenant_a)

        item = next(doc for doc in documents if doc["document_id"] == document_id)
        assert item == {
            "document_id": document_id,
            "title": "A 租户课程说明",
            "source": "ui://tenant-upload/document-list-test",
            "version": 1,
            "preview_lines": [
                "第一行：课程介绍",
                "第二行：适合三年级",
                "第三行：周末上课",
            ],
            "content": content,
        }
        assert all(doc["title"] != "B 租户私有说明" for doc in documents)
    finally:
        await store.delete_document(tenant_a, document_id)
        await store.delete_document(tenant_b, document_id)
        await store.client.close()


async def test_suggested_questions_are_deduplicated_and_tenant_scoped():
    store = TenantScopedVectorStore()
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    document_id = "suggestion-isolation-test"
    try:
        chunks_a = build_document_chunks(
            tenant_id=tenant_a,
            document_id=document_id,
            title="英语课程",
            source="test://suggestions",
            content="启蒙英语适合 4-6 岁。",
            version=1,
        )
        chunks_a[0]["payload"]["suggested_questions"] = [
            "有哪些英语课程？",
            "启蒙英语适合几岁？",
            "有哪些英语课程？",
        ]
        chunks_b = build_document_chunks(
            tenant_id=tenant_b,
            document_id=document_id,
            title="数学课程",
            source="test://suggestions",
            content="数学竞赛班每周六上课。",
            version=1,
        )
        chunks_b[0]["payload"]["suggested_questions"] = ["数学班几点上课？"]
        await store.upsert_chunks(tenant_a, chunks_a)
        await store.upsert_chunks(tenant_b, chunks_b)

        suggestions = await store.list_suggested_questions(tenant_a, limit=8)

        assert suggestions == ["有哪些英语课程？", "启蒙英语适合几岁？"]
        assert "数学班几点上课？" not in suggestions
    finally:
        await store.delete_document(tenant_a, document_id)
        await store.delete_document(tenant_b, document_id)
        await store.client.close()
