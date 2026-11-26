"""Durable journal for participant PDF filename migration.

Revision ID: 20261126_0052
Revises: b0be20605ea7
"""

from alembic import op
import sqlalchemy as sa


revision = "20261126_0052"
down_revision = "b0be20605ea7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "uploaded_document_renames",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("migration_id", sa.String(64), nullable=False),
        sa.Column("submission_file_id", sa.Integer(), sa.ForeignKey("submission_files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("old_filename", sa.String(512), nullable=False),
        sa.Column("old_storage_path", sa.Text(), nullable=False),
        sa.Column("base_filename", sa.String(512), nullable=False),
        sa.Column("new_filename", sa.String(512), nullable=False),
        sa.Column("new_storage_path", sa.Text(), nullable=False),
        sa.Column("staging_storage_path", sa.Text(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("migration_id", "submission_file_id", name="uq_uploaded_document_renames_migration_file"),
    )
    op.create_index("ix_uploaded_document_renames_migration_id", "uploaded_document_renames", ["migration_id"])
    op.create_index("ix_uploaded_document_renames_file", "uploaded_document_renames", ["submission_file_id"])
    op.create_index("ix_uploaded_document_renames_group", "uploaded_document_renames", ["migration_id", "group_id"])
    op.create_index("ix_uploaded_document_renames_status", "uploaded_document_renames", ["status"])


def downgrade():
    connection = op.get_bind()
    count = connection.execute(sa.text("SELECT COUNT(*) FROM uploaded_document_renames")).scalar_one()
    if count:
        raise RuntimeError("Rename audit journal is not empty; refusing to discard migration history.")
    op.drop_index("ix_uploaded_document_renames_status", table_name="uploaded_document_renames")
    op.drop_index("ix_uploaded_document_renames_file", table_name="uploaded_document_renames")
    op.drop_index("ix_uploaded_document_renames_group", table_name="uploaded_document_renames")
    op.drop_index("ix_uploaded_document_renames_migration_id", table_name="uploaded_document_renames")
    op.drop_table("uploaded_document_renames")
