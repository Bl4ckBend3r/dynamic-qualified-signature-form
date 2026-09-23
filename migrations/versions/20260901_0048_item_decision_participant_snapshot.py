"""item decision participant snapshot

Revision ID: 20260901_0048
Revises: 20260831_0047
Create Date: 2026-09-01
"""

from alembic import op
import sqlalchemy as sa


revision = "20260901_0048"
down_revision = "20260831_0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("repeatable_group_item_decisions") as batch:
        batch.add_column(
            sa.Column("participant_snapshot_json", sa.JSON(), nullable=False, server_default="{}")
        )


def downgrade() -> None:
    with op.batch_alter_table("repeatable_group_item_decisions") as batch:
        batch.drop_column("participant_snapshot_json")
