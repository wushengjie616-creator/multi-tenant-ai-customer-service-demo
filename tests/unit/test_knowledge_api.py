from unittest.mock import AsyncMock

from app.api import knowledge


async def test_reindex_replaces_existing_document_for_current_tenant(monkeypatch):
    store = AsyncMock()
    monkeypatch.setattr(knowledge, "vector_store", store)
    monkeypatch.setattr(
        knowledge, "build_enriched_document_chunks",
        AsyncMock(return_value=[{
            "chunk_id": "doc-1:2:0", "point_id": "point-1", "vector": [0.0] * 64,
            "payload": {"content_hash": "hash", "enrichment_status": "enriched"},
        }]),
    )
    graph = AsyncMock()
    graph.replace_document = AsyncMock(return_value={"nodes": 0, "edges": 0, "es_documents": 1})
    monkeypatch.setattr(knowledge, "graph_store", graph)
    session = AsyncMock()
    payload = knowledge.DocumentRequest(
        document_id="doc-1", title="课程", source="upload", content="新内容", version=2,
    )
    auth = type("Auth", (), {"tenant_id": "tenant-a"})()
    result = await knowledge.reindex_document(payload, auth, session)
    store.delete_document.assert_awaited_once_with("tenant-a", "doc-1")
    store.upsert_chunks.assert_awaited_once()
    graph.replace_document.assert_awaited_once()
    assert result["document_id"] == "doc-1" and result["chunks"] == 1
    assert result["graph"] == {"nodes": 0, "edges": 0, "es_documents": 1}
