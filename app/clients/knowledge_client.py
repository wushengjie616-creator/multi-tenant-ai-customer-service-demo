"""强制租户与治理过滤的 Qdrant 适配器。"""

from datetime import datetime, timezone

from qdrant_client import AsyncQdrantClient, models

from app.core.config import settings

COLLECTION = "education_knowledge"
VECTOR_SIZE = 64


def is_payload_active(payload: dict, now: datetime | None = None) -> bool:
    current = now or datetime.now(timezone.utc)
    try:
        start = datetime.fromisoformat(payload["effective_from"].replace("Z", "+00:00")) if payload.get("effective_from") else None
        end = datetime.fromisoformat(payload["effective_until"].replace("Z", "+00:00")) if payload.get("effective_until") else None
        if start and start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end and end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return False
    return (start is None or start <= current) and (end is None or end > current)


class TenantScopedVectorStore:
    def __init__(self, client: AsyncQdrantClient | None = None):
        self.client = client or AsyncQdrantClient(url=settings.qdrant_url)
        self._indexes_ready = False

    @staticmethod
    def _governance_conditions(tenant_id: str) -> list[models.FieldCondition]:
        return [
            models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id)),
            models.FieldCondition(key="visibility", match=models.MatchValue(value="public")),
            models.FieldCondition(key="review_status", match=models.MatchValue(value="approved")),
        ]

    async def ensure_collection(self) -> None:
        if not await self.client.collection_exists(COLLECTION):
            await self.client.create_collection(COLLECTION, vectors_config=models.VectorParams(size=VECTOR_SIZE, distance=models.Distance.COSINE))
        if not self._indexes_ready:
            for field in (
                "tenant_id", "document_id", "visibility", "review_status",
                "entities", "aliases", "topics", "keywords",
            ):
                await self.client.create_payload_index(
                    COLLECTION, field, models.PayloadSchemaType.KEYWORD
                )
            self._indexes_ready = True

    async def upsert_chunks(self, tenant_id: str, chunks: list[dict]) -> None:
        if not tenant_id or any(not c.get("chunk_id") or len(c.get("vector", [])) != VECTOR_SIZE for c in chunks):
            raise ValueError("invalid tenant or chunk")
        points = []
        for chunk in chunks:
            payload = {**chunk["payload"], "tenant_id": tenant_id}
            points.append(models.PointStruct(id=chunk["point_id"], vector=chunk["vector"], payload=payload))
        await self.ensure_collection()
        await self.client.upsert(COLLECTION, points=points, wait=True)

    async def search(self, tenant_id: str, vector: list[float], limit: int = 5) -> list[dict]:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        query_filter = models.Filter(must=self._governance_conditions(tenant_id))
        # Qdrant server is pinned to 1.9; use the stable /points/search API rather
        # than the newer universal query endpoint introduced by later servers.
        points = await self.client.search(
            COLLECTION,
            query_vector=vector,
            query_filter=query_filter,
            limit=max(limit * 3, limit),
            with_payload=True,
        )
        return [
            {**(point.payload or {}), "score": point.score}
            for point in points if is_payload_active(point.payload or {})
        ][:limit]

    async def search_by_entities(
        self, tenant_id: str, entities: list[str], limit: int = 16
    ) -> list[dict]:
        """Retrieve tenant-scoped points matching any enriched entity field."""
        normalized = list(dict.fromkeys(value.strip() for value in entities if value.strip()))[:32]
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if not normalized:
            return []
        await self.ensure_collection()
        points, _ = await self.client.scroll(
            COLLECTION,
            scroll_filter=models.Filter(
                must=self._governance_conditions(tenant_id),
                should=[
                    models.FieldCondition(
                        key=field, match=models.MatchAny(any=normalized)
                    )
                    for field in ("entities", "aliases", "topics", "keywords")
                ],
            ),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        result = []
        query_set = set(normalized)
        for point in points:
            payload = point.payload or {}
            if not is_payload_active(payload):
                continue
            matched = query_set.intersection(
                item
                for field in ("entities", "aliases", "topics", "keywords")
                for item in payload.get(field, [])
            )
            result.append({**payload, "score": 0.0, "entity_matches": len(matched)})
        return result

    async def search_by_text(
        self, tenant_id: str, terms: list[str], limit: int = 16
    ) -> list[dict]:
        """Full-text fallback over enriched search_text, still tenant/governance scoped."""
        normalized = list(
            dict.fromkeys(value.strip() for value in terms if len(value.strip()) >= 2)
        )[:16]
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if not normalized:
            return []
        await self.ensure_collection()
        results: list[dict] = []
        offset = None
        remaining = max(settings.knowledge_lexical_scan_limit, 1)
        normalized_lower = [term.lower() for term in normalized]
        while remaining > 0 and len(results) < limit:
            batch_size = min(100, remaining)
            points, next_offset = await self.client.scroll(
                COLLECTION,
                scroll_filter=models.Filter(
                    must=self._governance_conditions(tenant_id)
                ),
                limit=batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                haystack = "".join(str(payload.get("search_text", "")).lower().split())
                if is_payload_active(payload) and any(
                    "".join(term.split()) in haystack for term in normalized_lower
                ):
                    results.append({**payload, "score": 0.0})
                    if len(results) >= limit:
                        break
            remaining -= len(points)
            if next_offset is None or not points:
                break
            offset = next_offset
        return results

    async def list_documents(self, tenant_id: str) -> list[dict]:
        """List documents reconstructed from this tenant's Qdrant payloads."""
        if not tenant_id:
            raise ValueError("tenant_id is required")
        await self.ensure_collection()
        grouped: dict[str, list[dict]] = {}
        offset = None
        while True:
            points, next_offset = await self.client.scroll(
                COLLECTION,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="tenant_id", match=models.MatchValue(value=tenant_id)
                        )
                    ]
                ),
                limit=100,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                document_id = str(payload.get("document_id", "")).strip()
                if document_id:
                    grouped.setdefault(document_id, []).append(payload)
            if next_offset is None or not points:
                break
            offset = next_offset

        documents = []
        for document_id, payloads in grouped.items():
            latest_version = max(int(item.get("version", 1)) for item in payloads)
            chunks = sorted(
                (
                    item
                    for item in payloads
                    if int(item.get("version", 1)) == latest_version
                ),
                key=lambda item: int(item.get("chunk_index", 0)),
            )
            first = chunks[0]
            content = next(
                (
                    str(item["document_content"])
                    for item in chunks
                    if item.get("document_content") is not None
                ),
                "\n\n".join(str(item.get("content", "")) for item in chunks),
            )
            preview_lines = [
                line.strip() for line in content.splitlines() if line.strip()
            ][:3]
            documents.append(
                {
                    "document_id": document_id,
                    "title": str(first.get("title") or document_id),
                    "source": str(first.get("source") or ""),
                    "version": latest_version,
                    "preview_lines": preview_lines,
                    "content": content,
                }
            )
        return sorted(
            documents,
            key=lambda item: (item["title"].casefold(), item["document_id"]),
        )

    async def list_candidate_chunks(
        self, tenant_id: str, *, limit: int = 40
    ) -> list[dict]:
        """Return a bounded, governed tenant corpus for semantic reranking."""
        if not tenant_id:
            raise ValueError("tenant_id is required")
        await self.ensure_collection()
        points, _ = await self.client.scroll(
            COLLECTION,
            scroll_filter=models.Filter(
                must=self._governance_conditions(tenant_id)
            ),
            limit=max(1, limit),
            with_payload=True,
            with_vectors=False,
        )
        return [
            {**(point.payload or {}), "score": 0.0}
            for point in points
            if is_payload_active(point.payload or {})
        ][:limit]

    async def list_suggested_questions(
        self, tenant_id: str, *, limit: int = 8
    ) -> list[str]:
        """Collect deduplicated questions from the current tenant's indexed chunks."""
        candidates = await self.list_candidate_chunks(
            tenant_id, limit=max(settings.knowledge_lexical_scan_limit, limit)
        )
        suggestions: list[str] = []
        legacy_titles: list[str] = []
        for payload in candidates:
            payload_questions = payload.get("suggested_questions", [])
            for question in payload_questions:
                normalized = " ".join(str(question).split())
                if normalized and normalized not in suggestions:
                    suggestions.append(normalized)
                    if len(suggestions) >= limit:
                        return suggestions
            if not payload_questions and int(payload.get("chunk_index", 0)) == 0:
                title = str(payload.get("title", "")).strip()
                if title and title not in legacy_titles:
                    legacy_titles.append(title)
        for title in legacy_titles:
            question = f"请介绍一下{title}？"
            if question not in suggestions:
                suggestions.append(question)
                if len(suggestions) >= limit:
                    break
        return suggestions

    async def delete_document(self, tenant_id: str, document_id: str) -> None:
        await self.ensure_collection()
        await self.client.delete(COLLECTION, points_selector=models.FilterSelector(filter=models.Filter(must=[models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id)), models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))])), wait=True)


vector_store = TenantScopedVectorStore()
