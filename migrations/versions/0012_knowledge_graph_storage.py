"""knowledge graph storage: 原文 / ES 文档 / 图节点 / 图边 四层

Revision ID: 0012
Revises: 0011
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("knowledge_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.String(128), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("source", sa.String(255), server_default="", nullable=False),
        sa.Column("format", sa.String(32), server_default="markdown", nullable=False),
        sa.Column("content", sa.Text(), server_default="", nullable=False),
        sa.Column("content_hash", sa.String(64), server_default="", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("visibility", sa.String(32), server_default="public", nullable=False),
        sa.Column("review_status", sa.String(32), server_default="approved", nullable=False),
        sa.Column("effective_from", sa.String(64), nullable=True),
        sa.Column("effective_until", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "document_id", "version", name="uq_knowledge_doc_tenant_version"))
    op.create_index("ix_knowledge_documents_tenant_id", "knowledge_documents", ["tenant_id"])
    op.create_index("ix_knowledge_documents_document_id", "knowledge_documents", ["document_id"])

    op.create_table("knowledge_es_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("es_doc_id", sa.String(160), nullable=False),
        sa.Column("document_id", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(32), server_default="document", nullable=False),
        sa.Column("title", sa.String(255), server_default="", nullable=False),
        sa.Column("body", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("body_text", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "es_doc_id", name="uq_knowledge_es_tenant_doc"))
    op.create_index("ix_knowledge_es_documents_tenant_id", "knowledge_es_documents", ["tenant_id"])
    op.create_index("ix_knowledge_es_documents_document_id", "knowledge_es_documents", ["document_id"])

    op.create_table("knowledge_graph_nodes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_id", sa.String(160), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("document_id", sa.String(128), server_default="", nullable=False),
        sa.Column("aliases", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("attributes", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("es_doc_id", sa.String(160), nullable=True),
        sa.Column("schema_version", sa.String(16), server_default="v1", nullable=False),
        sa.Column("effective_from", sa.String(64), nullable=True),
        sa.Column("effective_until", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "node_id", name="uq_knowledge_node_tenant_node"))
    op.create_index("ix_knowledge_graph_nodes_tenant_id", "knowledge_graph_nodes", ["tenant_id"])
    op.create_index("ix_knowledge_nodes_tenant_type", "knowledge_graph_nodes", ["tenant_id", "type"])
    op.create_index("ix_knowledge_nodes_tenant_doc", "knowledge_graph_nodes", ["tenant_id", "document_id"])

    op.create_table("knowledge_graph_edges",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("edge_id", sa.String(160), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("from_node_id", sa.String(160), nullable=False),
        sa.Column("to_node_id", sa.String(160), nullable=False),
        sa.Column("document_id", sa.String(128), server_default="", nullable=False),
        sa.Column("schema_version", sa.String(16), server_default="v1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "edge_id", name="uq_knowledge_edge_tenant_edge"))
    op.create_index("ix_knowledge_graph_edges_tenant_id", "knowledge_graph_edges", ["tenant_id"])
    op.create_index("ix_knowledge_edges_tenant_from", "knowledge_graph_edges", ["tenant_id", "from_node_id", "type"])
    op.create_index("ix_knowledge_edges_tenant_to", "knowledge_graph_edges", ["tenant_id", "to_node_id", "type"])
    op.create_index("ix_knowledge_edges_tenant_doc", "knowledge_graph_edges", ["tenant_id", "document_id"])


def downgrade() -> None:
    op.drop_table("knowledge_graph_edges")
    op.drop_table("knowledge_graph_nodes")
    op.drop_table("knowledge_es_documents")
    op.drop_table("knowledge_documents")
