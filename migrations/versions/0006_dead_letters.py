"""persist dead letters

Revision ID: 0006
Revises: 0005
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dead_letters",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("original_event_id", sa.String(128)),
        sa.Column("original_subject", sa.String(128), nullable=False),
        sa.Column("error_type", sa.String(128), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.String(24), server_default="open", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("replayed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_dead_letters_tenant_id", "dead_letters", ["tenant_id"])
    op.create_index("ix_dead_letters_trace_id", "dead_letters", ["trace_id"])


def downgrade() -> None:
    op.drop_table("dead_letters")
