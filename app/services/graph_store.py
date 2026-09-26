"""知识图谱四层存储：PG 为事实源，delete-then-insert 原子替换。

存储选型（对应 docs/optimization/rag-knowledge-graph-extraction-schema.md §1）：

- **PG = system-of-record**：四层各一张表，`tenant_id` 为第一列 + 复合索引，检索硬过滤。
- **jsonl 只做离线抽取中间产物 / 审计 / 可重跑输出**，不承担在线可查询事实源。
- **Qdrant = 可重建加速器**：chunk/向量照旧，图/ES 不替代它，是新增召回层。

`replace_document` 是「抽取 + 四层落库」的唯一写入口，事务内 delete-then-insert 保证
同文档替换的原子性与幂等（重复导入同一 document_id 覆盖旧图，不产生孤儿节点）。
"""

import hashlib
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_graph import (
    KnowledgeDocument,
    KnowledgeESDocument,
    KnowledgeGraphEdge,
    KnowledgeGraphNode,
)
from app.services.graph_extraction import SCHEMA_VERSION, extract_graph


class GraphStore:
    """四层知识存储的持久化入口（每方法自管 commit，与现有 service 约定一致）。"""

    async def replace_document(
        self,
        session: AsyncSession,
        tenant_id: str,
        *,
        document_id: str,
        title: str,
        source: str,
        content: str,
        version: int,
        effective_from: str | None = None,
        effective_until: str | None = None,
    ) -> dict[str, int]:
        """替换单个文档的原文 / ES / 节点 / 边四层存储。

        抽取失败会向上抛出（图写是入库主流程的一环，不吞错）；同一 document_id
        重复导入时先删旧再插新，保证无孤儿。
        """
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        graph = extract_graph(
            tenant_id=tenant_id,
            document_id=document_id,
            title=title,
            content=content,
        )

        # 1) 原文层：不可变原始文本 + 内容哈希
        await session.execute(
            delete(KnowledgeDocument).where(
                KnowledgeDocument.tenant_id == tenant_id,
                KnowledgeDocument.document_id == document_id,
            )
        )
        session.add(
            KnowledgeDocument(
                tenant_id=tenant_id,
                document_id=document_id,
                title=title,
                source=source,
                content=content,
                content_hash=content_hash,
                version=version,
                effective_from=effective_from,
                effective_until=effective_until,
            )
        )

        # 2) ES 文档层：长文本 / 半结构化详情（真实 ES 可无缝替换本 PG 兜底实现）
        await session.execute(
            delete(KnowledgeESDocument).where(
                KnowledgeESDocument.tenant_id == tenant_id,
                KnowledgeESDocument.document_id == document_id,
            )
        )
        es_doc_id = f"es:{document_id}:{version}"
        session.add(
            KnowledgeESDocument(
                tenant_id=tenant_id,
                es_doc_id=es_doc_id,
                document_id=document_id,
                kind="document",
                title=title,
                body={"source": source, "version": version, "content_hash": content_hash},
                body_text=content,
            )
        )

        # 3/4) 节点 / 边层
        await session.execute(
            delete(KnowledgeGraphNode).where(
                KnowledgeGraphNode.tenant_id == tenant_id,
                KnowledgeGraphNode.document_id == document_id,
            )
        )
        await session.execute(
            delete(KnowledgeGraphEdge).where(
                KnowledgeGraphEdge.tenant_id == tenant_id,
                KnowledgeGraphEdge.document_id == document_id,
            )
        )
        for node in graph["nodes"]:
            session.add(
                KnowledgeGraphNode(
                    tenant_id=tenant_id,
                    node_id=node["node_id"],
                    type=node["type"],
                    name=node["name"],
                    document_id=document_id,
                    aliases=node["aliases"],
                    attributes=node["attributes"],
                    es_doc_id=node.get("es_doc_id"),
                    schema_version=SCHEMA_VERSION,
                    effective_from=effective_from,
                    effective_until=effective_until,
                )
            )
        for edge in graph["edges"]:
            session.add(
                KnowledgeGraphEdge(
                    tenant_id=tenant_id,
                    edge_id=edge["edge_id"],
                    type=edge["type"],
                    from_node_id=edge["from_node_id"],
                    to_node_id=edge["to_node_id"],
                    document_id=document_id,
                    schema_version=SCHEMA_VERSION,
                )
            )

        await session.commit()
        return {
            "nodes": len(graph["nodes"]),
            "edges": len(graph["edges"]),
            "es_documents": 1,
        }

    async def list_graph(
        self,
        session: AsyncSession,
        tenant_id: str,
        *,
        limit: int = 500,
    ) -> dict[str, Any]:
        """只读视图：返回该租户的节点与边（供 `GET /knowledge/graph` 与调试大盘）。"""
        nodes = (
            await session.execute(
                select(KnowledgeGraphNode)
                .where(KnowledgeGraphNode.tenant_id == tenant_id)
                .order_by(KnowledgeGraphNode.type, KnowledgeGraphNode.name)
                .limit(limit)
            )
        ).scalars().all()
        edges = (
            await session.execute(
                select(KnowledgeGraphEdge)
                .where(KnowledgeGraphEdge.tenant_id == tenant_id)
                .order_by(KnowledgeGraphEdge.type)
                .limit(limit)
            )
        ).scalars().all()
        return {
            "nodes": [
                {
                    "node_id": node.node_id,
                    "type": node.type,
                    "name": node.name,
                    "aliases": node.aliases,
                    "attributes": node.attributes,
                    "document_id": node.document_id,
                }
                for node in nodes
            ],
            "edges": [
                {
                    "edge_id": edge.edge_id,
                    "type": edge.type,
                    "from_node_id": edge.from_node_id,
                    "to_node_id": edge.to_node_id,
                    "document_id": edge.document_id,
                }
                for edge in edges
            ],
        }

    async def list_classes(
        self,
        session: AsyncSession,
        tenant_id: str,
        *,
        limit: int = 200,
    ) -> list[dict]:
        """返回该租户的 class 节点（班型池），供课程咨询推荐引擎使用。"""
        rows = (
            await session.execute(
                select(KnowledgeGraphNode)
                .where(
                    KnowledgeGraphNode.tenant_id == tenant_id,
                    KnowledgeGraphNode.type == "class",
                )
                .order_by(KnowledgeGraphNode.name)
                .limit(limit)
            )
        ).scalars().all()
        return [
            {
                "node_id": node.node_id,
                "name": node.name,
                "aliases": node.aliases,
                "attributes": node.attributes,
                "document_id": node.document_id,
            }
            for node in rows
        ]


graph_store = GraphStore()
