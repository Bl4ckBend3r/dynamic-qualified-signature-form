from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260813_0033"
down_revision = "20260813_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "forms",
        sa.Column(
            "is_listed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )

    op.add_column(
        "forms",
        sa.Column(
            "share_token_hash",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column(
        "forms",
        "share_token_hash",
    )

    op.drop_column(
        "forms",
        "is_listed",
    )