"""知识四层存储模型：原文 / ES 文档 / 图节点 / 图边。

存储方案（对应 docs/optimization/rag-knowledge-graph-extraction-schema.md）：

- **PG 为事实源（system-of-record）**，四层各一张表，`tenant_id` 为第一列 + 复合索引，
  检索时硬过滤（与 Qdrant `_governance_conditions` 同级，租户隔离等价）。
- **jsonl 只做离线抽取中间产物 / 审计 / 可重跑输出**，不承担在线可查询事实源职责
  （无租户隔离、无反向边 `to` 索引、无事务、无备份，故不入库当源）。
- 四层分工：原文（不可变原始文本 + 内容哈希）→ ES 文档（长文本/半结构化详情）→
  节点（类型化实体，属性 JSONB，注册表约束）→ 边（有向关系，from/to 双向索引支撑反向遍历）。

`KnowledgeDocument` / `KnowledgeESDocument` 与 Qdrant 侧 chunk/向量**互补**，不是替代。
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class KnowledgeDocument(Base):
    """原文层：不可变原始文本 + 元数据 + 内容哈希（幂等重跑 / 去重依据）。"""

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "document_id", "version", name="uq_knowledge_doc_tenant_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    format: Mapped[str] = mapped_column(String(32), nullable=False, server_default="markdown")
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, server_default="public")
    review_status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="approved")
    effective_from: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effective_until: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class KnowledgeESDocument(Base):
    """ES 文档层：长文本 / 半结构化详情。真实 ES 可无缝替换本表的 PG 兜底实现。"""

    __tablename__ = "knowledge_es_documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "es_doc_id", name="uq_knowledge_es_tenant_doc"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    es_doc_id: Mapped[str] = mapped_column(String(160), nullable=False)
    document_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, server_default="document")
    title: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    body: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    body_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KnowledgeGraphNode(Base):
    """节点层：类型化实体（type ∈ 注册表），属性 JSONB，节点 id 稳定哈希可跨文档对齐。"""

    __tablename__ = "knowledge_graph_nodes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "node_id", name="uq_knowledge_node_tenant_node"),
        Index("ix_knowledge_nodes_tenant_type", "tenant_id", "type"),
        Index("ix_knowledge_nodes_tenant_doc", "tenant_id", "document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_id: Mapped[str] = mapped_column(String(160), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    document_id: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    aliases: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    es_doc_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, server_default="v1")
    effective_from: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effective_until: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class KnowledgeGraphEdge(Base):
    """边层：有向关系拓扑。from/to 双向索引支撑「老师带哪些班」正向与「某班谁教」反向遍历。"""

    __tablename__ = "knowledge_graph_edges"
    __table_args__ = (
        UniqueConstraint("tenant_id", "edge_id", name="uq_knowledge_edge_tenant_edge"),
        Index("ix_knowledge_edges_tenant_from", "tenant_id", "from_node_id", "type"),
        Index("ix_knowledge_edges_tenant_to", "tenant_id", "to_node_id", "type"),
        Index("ix_knowledge_edges_tenant_doc", "tenant_id", "document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    edge_id: Mapped[str] = mapped_column(String(160), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    from_node_id: Mapped[str] = mapped_column(String(160), nullable=False)
    to_node_id: Mapped[str] = mapped_column(String(160), nullable=False)
    document_id: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, server_default="v1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
