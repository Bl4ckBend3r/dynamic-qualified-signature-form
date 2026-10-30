"""record final agreements sourced from the office Nextcloud folder

Revision ID: 20260729_0025
Revises: 20260727_0024
"""

from alembic import op
import sqlalchemy as sa


revision = "20260729_0025"
down_revision = "20260727_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "submission_files" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("submission_files")}
    for name, length in (("file_role", 128), ("storage_provider", 64)):
        if name not in existing:
            op.add_column("submission_files", sa.Column(name, sa.String(length), nullable=False, server_default=""))


def downgrade() -> None:
    # Kept intentionally non-destructive: these fields preserve audit metadata.
    pass
