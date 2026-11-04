"""repair internal note revision importance columns

Revision ID: 20260818_0042
Revises: 20260818_0041
Create Date: 2026-08-18
"""

from alembic import op
import sqlalchemy as sa


revision = "20260818_0042"
down_revision = "20260818_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "submission_internal_note_revisions" not in inspector.get_table_names():
        return
    columns = {
        column["name"]
        for column in inspector.get_columns("submission_internal_note_revisions")
    }
    with op.batch_alter_table("submission_internal_note_revisions") as batch:
        if "previous_is_important" not in columns:
            batch.add_column(sa.Column(
                "previous_is_important", sa.Boolean(), nullable=False,
                server_default=sa.false(),
            ))
        if "new_is_important" not in columns:
            batch.add_column(sa.Column(
                "new_is_important", sa.Boolean(), nullable=False,
                server_default=sa.false(),
            ))


def downgrade() -> None:
    # This is a convergence migration. On correctly migrated databases these
    # columns predate 0042, so a downgrade must not remove canonical history.
    pass
