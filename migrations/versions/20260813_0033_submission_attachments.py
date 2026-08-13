"""add versioned submission attachment metadata

Revision ID: 20260813_0033
Revises: 20260813_0032
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa

revision = "20260813_0033"
down_revision = "20260813_0032"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("submission_files") as batch:
        batch.add_column(sa.Column("field_key", sa.String(255), nullable=False, server_default=""))
        batch.add_column(sa.Column("attachment_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("category", sa.String(128), nullable=False, server_default=""))
        batch.add_column(sa.Column("uploaded_by_source", sa.String(64), nullable=False, server_default=""))
        batch.add_column(sa.Column("workflow_step_at_upload", sa.String(128), nullable=False, server_default=""))
        batch.add_column(sa.Column("antivirus_status", sa.String(64), nullable=False, server_default="not_configured"))
        batch.add_column(sa.Column("rejection_reason", sa.Text(), nullable=False, server_default=""))
        batch.create_index("ix_submission_files_attachment_field", ["submission_id", "field_key", "attachment_version"])


def downgrade():
    with op.batch_alter_table("submission_files") as batch:
        batch.drop_index("ix_submission_files_attachment_field")
        for column in ("rejection_reason", "antivirus_status", "workflow_step_at_upload", "uploaded_by_source", "category", "attachment_version", "field_key"):
            batch.drop_column(column)
