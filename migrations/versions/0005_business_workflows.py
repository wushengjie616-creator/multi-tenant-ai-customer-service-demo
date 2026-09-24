"""business workflow persistence

Revision ID: 0005
Revises: 0004
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("confirmations",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(64), nullable=False), sa.Column("resource_id", sa.String(128), nullable=False), sa.Column("args_hash", sa.String(64), nullable=False),
        sa.Column("arguments", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False), sa.Column("status", sa.String(32), server_default="pending_confirmation", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_confirmations_tenant_id", "confirmations", ["tenant_id"]); op.create_index("ix_confirmations_user_id", "confirmations", ["user_id"])
    op.create_table("tool_executions",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False), sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(64), nullable=False), sa.Column("resource_id", sa.String(128), nullable=False), sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True), sa.Column("status", sa.String(32), server_default="executing", nullable=False),
        sa.Column("result", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_tool_executions_tenant_id", "tool_executions", ["tenant_id"])
    op.create_table("reminders",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False), sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False), sa.Column("content", sa.Text(), nullable=False), sa.Column("timezone", sa.String(64), nullable=False), sa.Column("repeat", sa.String(16), server_default="once", nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False), sa.Column("status", sa.String(24), server_default="active", nullable=False), sa.Column("version", sa.Integer(), server_default="1", nullable=False), sa.Column("lease_until", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_reminders_tenant_id", "reminders", ["tenant_id"]); op.create_index("ix_reminders_user_id", "reminders", ["user_id"]); op.create_index("ix_reminders_next_run_at", "reminders", ["next_run_at"])
    op.create_table("reminder_deliveries", sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("reminder_id", sa.Uuid(), sa.ForeignKey("reminders.id", ondelete="CASCADE"), nullable=False), sa.Column("occurrence_at", sa.DateTime(timezone=True), nullable=False), sa.Column("status", sa.String(24), server_default="pending", nullable=False), sa.Column("attempts", sa.Integer(), server_default="0", nullable=False), sa.Column("delivered_at", sa.DateTime(timezone=True)), sa.UniqueConstraint("reminder_id", "occurrence_at", name="uq_reminder_occurrence"))
    op.create_table("handoffs",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False), sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(24), server_default="handoff_pending", nullable=False), sa.Column("active_key", sa.String(16), server_default="active"), sa.Column("summary", sa.Text(), server_default="", nullable=False), sa.Column("reason", sa.String(255), nullable=False), sa.Column("context", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.UniqueConstraint("conversation_id", "active_key", name="uq_active_handoff"))
    op.create_index("ix_handoffs_tenant_id", "handoffs", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("handoffs"); op.drop_table("reminder_deliveries"); op.drop_table("reminders"); op.drop_table("tool_executions"); op.drop_table("confirmations")
