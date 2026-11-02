"""Archive submissions without deleting related records or Nextcloud files.

Revision ID: 20261102_0049
Revises: 20260901_0048
"""

from alembic import op
import sqlalchemy as sa


revision = "20261102_0049"
down_revision = "20260901_0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("form_submissions") as batch:
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("deleted_by_user_id", sa.Integer(), nullable=True))
        batch.create_index("ix_form_submissions_deleted_at", ["deleted_at"])


def downgrade() -> None:
    archived = op.get_bind().execute(
        sa.text("SELECT 1 FROM form_submissions WHERE deleted_at IS NOT NULL LIMIT 1")
    ).first()
    if archived:
        raise RuntimeError("Cannot downgrade while archived submissions exist: this would erase archive markers")
    with op.batch_alter_table("form_submissions") as batch:
        batch.drop_index("ix_form_submissions_deleted_at")
        batch.drop_column("deleted_by_user_id")
        batch.drop_column("deleted_at")
