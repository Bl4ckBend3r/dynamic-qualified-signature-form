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
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "submission_files" not in inspector.get_table_names():
        raise RuntimeError("Brak wymaganej tabeli submission_files przed migracją 20260813_0033.")
    existing = {item["name"] for item in inspector.get_columns("submission_files")}
    definitions = (
        sa.Column("field_key", sa.String(255), nullable=False, server_default=""),
        sa.Column("attachment_version", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(128), nullable=False, server_default=""),
        sa.Column("uploaded_by_source", sa.String(64), nullable=False, server_default=""),
        sa.Column("workflow_step_at_upload", sa.String(128), nullable=False, server_default=""),
        sa.Column("antivirus_status", sa.String(64), nullable=False, server_default="not_configured"),
        sa.Column("rejection_reason", sa.Text(), nullable=False, server_default=""),
    )
    with op.batch_alter_table("submission_files") as batch:
        for column in definitions:
            if column.name not in existing:
                batch.add_column(column)
    inspector = sa.inspect(bind)
    indexes = {item["name"] for item in inspector.get_indexes("submission_files")}
    if "ix_submission_files_attachment_field" not in indexes:
        op.create_index(
            "ix_submission_files_attachment_field",
            "submission_files",
            ["submission_id", "field_key", "attachment_version"],
        )


def downgrade():
    with op.batch_alter_table("submission_files") as batch:
        batch.drop_index("ix_submission_files_attachment_field")
        for column in ("rejection_reason", "antivirus_status", "workflow_step_at_upload", "uploaded_by_source", "category", "attachment_version", "field_key"):
            batch.drop_column(column)
