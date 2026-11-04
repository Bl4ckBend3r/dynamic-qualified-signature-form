"""add shared administrator login rate limiting

Revision ID: 20260826_0046
Revises: 20260824_0045
Create Date: 2026-08-26
"""

from alembic import op
import sqlalchemy as sa


revision = "20260826_0046"
down_revision = "20260824_0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "admin_login_attempts" not in inspector.get_table_names():
        op.create_table(
            "admin_login_attempts",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("key_hash", sa.String(length=64), nullable=False),
            sa.Column(
                "attempted_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("admin_login_attempts")}
    if "ix_admin_login_attempts_key_attempted" not in indexes:
        op.create_index(
            "ix_admin_login_attempts_key_attempted",
            "admin_login_attempts",
            ["key_hash", "attempted_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "admin_login_attempts" in sa.inspect(bind).get_table_names():
        op.drop_table("admin_login_attempts")
