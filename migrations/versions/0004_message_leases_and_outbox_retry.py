"""message processing lease and bounded outbox retry

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-23
"""

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "messages", sa.Column("processing_lease_until", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("outbox_events", sa.Column("tenant_id", sa.Uuid(), nullable=True))
    op.execute(
        "UPDATE outbox_events SET tenant_id = (payload->>'tenant_id')::uuid "
        "WHERE tenant_id IS NULL AND payload ? 'tenant_id'"
    )
    op.alter_column("outbox_events", "tenant_id", nullable=False)
    op.create_foreign_key(
        "fk_outbox_events_tenant_id_tenants",
        "outbox_events",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_outbox_events_tenant_id", "outbox_events", ["tenant_id"])
    op.add_column(
        "outbox_events",
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_outbox_events_next_attempt_at", "outbox_events", ["next_attempt_at"]
    )
    op.add_column(
        "outbox_events", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("outbox_events", "claimed_at")
    op.drop_index("ix_outbox_events_next_attempt_at", table_name="outbox_events")
    op.drop_column("outbox_events", "next_attempt_at")
    op.drop_index("ix_outbox_events_tenant_id", table_name="outbox_events")
    op.drop_constraint(
        "fk_outbox_events_tenant_id_tenants", "outbox_events", type_="foreignkey"
    )
    op.drop_column("outbox_events", "tenant_id")
    op.drop_column("messages", "processing_lease_until")
