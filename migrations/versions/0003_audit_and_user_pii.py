"""audit_logs + users 身份/PII 字段

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-23

- users 增加 email（租户内唯一）/ phone / full_name / password_hash
- 新建 audit_logs（审计，tenant 隔离）
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # users 身份与 PII 字段。先以 nullable 加列以兼容存量行，再 backfill 后收紧 NOT NULL
    # （0002 若已有 seed 用户，其 email/password_hash 为空，需先填充占位值）。
    op.add_column("users", sa.Column("email", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("phone", sa.String(length=32), nullable=True))
    op.add_column("users", sa.Column("full_name", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))

    # 存量行 backfill：email 用 id 派生占位（租户内唯一）；password_hash 用不可登录占位。
    op.execute(
        "UPDATE users SET email = 'legacy-' || id::text || '@example.com' WHERE email IS NULL"
    )
    op.execute("UPDATE users SET password_hash = '!locked' WHERE password_hash IS NULL")

    op.alter_column("users", "email", nullable=False)
    op.alter_column("users", "password_hash", nullable=False)
    op.create_unique_constraint(
        "uq_users_tenant_id_email", "users", ["tenant_id", "email"]
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("trace_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("detail", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_audit_logs_tenant_id", "audit_logs", ["tenant_id"])
    op.create_index("ix_audit_logs_trace_id", "audit_logs", ["trace_id"])
    op.create_index("ix_audit_logs_actor_id", "audit_logs", ["actor_id"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_constraint("uq_users_tenant_id_email", "users", type_="unique")
    op.drop_column("users", "password_hash")
    op.drop_column("users", "full_name")
    op.drop_column("users", "phone")
    op.drop_column("users", "email")
