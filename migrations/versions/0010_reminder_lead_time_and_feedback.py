"""Reminder lead time and persistent dissatisfaction count.

Revision ID: 0010
Revises: 0009
"""

from alembic import op
import sqlalchemy as sa


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("dissatisfaction_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "reminders",
        sa.Column("scheduled_for_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "reminders",
        sa.Column("lead_time_minutes", sa.Integer(), server_default="0", nullable=False),
    )
    op.execute("UPDATE reminders SET scheduled_for_at = next_run_at WHERE scheduled_for_at IS NULL")
    op.alter_column("reminders", "scheduled_for_at", nullable=False)


def downgrade() -> None:
    op.drop_column("reminders", "lead_time_minutes")
    op.drop_column("reminders", "scheduled_for_at")
    op.drop_column("conversations", "dissatisfaction_count")
