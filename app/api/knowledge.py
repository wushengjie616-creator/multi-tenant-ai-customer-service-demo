"""知识导入与问答 API。"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, require_roles
from app.clients.knowledge_client import vector_store
from app.clients.llm_client import llm_client
from app.core.config import settings
from app.core.database import get_session
from app.core.exceptions import AppError
from app.core.security import ROLE_ADMIN, AuthContext
from app.services.knowledge_ingestion import (
    build_enriched_document_chunks,
    extract_query_entities,
)
from app.services.graph_store import graph_store
from app.services.rag_service import answer_question

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class DocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    title: str
    source: str
    content: str = Field(min_length=1)
    version: int = 1
    effective_from: str | None = None
    effective_until: str | None = None


class QuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=1000)


@router.get("/documents")
async def list_documents(auth: AuthContext = Depends(require_roles(ROLE_ADMIN))):
    return {"documents": await vector_store.list_documents(auth.tenant_id)}


@router.get("/suggestions")
async def list_suggestions(auth: AuthContext = Depends(get_current_user)):
    return {
        "suggestions": await vector_store.list_suggested_questions(
            auth.tenant_id, limit=8
        )
    }


async def _replace_document(payload: DocumentRequest, tenant_id: str, session: AsyncSession) -> dict:
    if len(payload.content) > settings.demo_max_upload_chars:
        raise AppError(
            f"文档内容超过 {settings.demo_max_upload_chars} 字符限制",
            code="PAYLOAD_TOO_LARGE",
            status_code=413,
        )
    chunks = await build_enriched_document_chunks(
        tenant_id=tenant_id,
        llm=llm_client,
        **payload.model_dump(),
    )
    await vector_store.delete_document(tenant_id, payload.document_id)
    await vector_store.upsert_chunks(tenant_id, chunks)
    # 知识图谱四层落库（PG 事实源）。图写是入库主流程一环，失败不吞错、向上抛出。
    graph = await graph_store.replace_document(
        session,
        tenant_id,
        document_id=payload.document_id,
        title=payload.title,
        source=payload.source,
        content=payload.content,
        version=payload.version,
        effective_from=payload.effective_from,
        effective_until=payload.effective_until,
    )
    enrichment: dict[str, int] = {}
    for chunk in chunks:
        status = chunk["payload"]["enrichment_status"]
        enrichment[status] = enrichment.get(status, 0) + 1
    return {
        "document_id": payload.document_id,
        "chunks": len(chunks),
        "content_hash": chunks[0]["payload"]["content_hash"],
        "enrichment": enrichment,
        "graph": graph,
    }


@router.post("/documents")
async def import_document(payload: DocumentRequest, auth: AuthContext = Depends(require_roles(ROLE_ADMIN)), session: AsyncSession = Depends(get_session)):
    return await _replace_document(payload, auth.tenant_id, session)


@router.post("/reindex")
async def reindex_document(payload: DocumentRequest, auth: AuthContext = Depends(require_roles(ROLE_ADMIN)), session: AsyncSession = Depends(get_session)):
    """Explicitly replace every indexed chunk for a tenant-owned document."""
    return await _replace_document(payload, auth.tenant_id, session)


@router.get("/graph")
async def get_graph(auth: AuthContext = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    """只读视图：返回该租户的知识图谱节点与边（当前检索接线留空，仅存储 + 查看）。"""
    return await graph_store.list_graph(session, auth.tenant_id)


@router.post("/query")
async def query(payload: QuestionRequest, auth: AuthContext = Depends(get_current_user)):
    query_entities = await extract_query_entities(payload.question, llm_client)
    return await answer_question(
        vector_store,
        auth.tenant_id,
        payload.question,
        query_entities=query_entities,
        semantic_reranker=llm_client,
    )
