"""Bind idempotency keys to a canonical command request.

Revision ID: 0011
Revises: 0010
"""

from alembic import op
import sqlalchemy as sa


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing executions predate payload binding and remain readable. All new
    # executions populate this field and reject key reuse with another payload.
    op.add_column(
        "tool_executions",
        sa.Column("request_hash", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tool_executions", "request_hash")
