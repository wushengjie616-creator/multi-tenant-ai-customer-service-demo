"""Add customer-visible human handoff lifecycle.

Revision ID: 0009
Revises: 0008
"""

from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("handoffs", "status", type_=sa.String(32), server_default="pending")
    op.execute("UPDATE handoffs SET status = 'pending' WHERE status = 'handoff_pending'")
    op.execute("UPDATE handoffs SET status = 'in_progress' WHERE status = 'agent_connected'")
    op.add_column("handoffs", sa.Column("accepted_at", sa.DateTime(timezone=True)))
    op.add_column("handoffs", sa.Column("close_requested_at", sa.DateTime(timezone=True)))
    op.add_column("handoffs", sa.Column("close_deadline", sa.DateTime(timezone=True)))
    op.add_column("handoffs", sa.Column("ended_at", sa.DateTime(timezone=True)))
    op.create_index("ix_handoffs_close_deadline", "handoffs", ["close_deadline"])


def downgrade() -> None:
    op.drop_index("ix_handoffs_close_deadline", table_name="handoffs")
    op.drop_column("handoffs", "ended_at")
    op.drop_column("handoffs", "close_deadline")
    op.drop_column("handoffs", "close_requested_at")
    op.drop_column("handoffs", "accepted_at")
    op.execute("UPDATE handoffs SET status = 'handoff_pending' WHERE status = 'pending'")
    op.execute("UPDATE handoffs SET status = 'agent_connected' WHERE status = 'in_progress'")
    op.alter_column("handoffs", "status", type_=sa.String(24), server_default="handoff_pending")
