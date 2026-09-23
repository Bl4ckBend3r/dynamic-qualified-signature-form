"""add unlisted form access and bridge the historic duplicate 0033

Revision ID: 20260813_0034
Revises: 20260813_0033
Create Date: 2026-08-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260813_0034"
down_revision = "20260813_0033"
branch_labels = None
depends_on = None


def _columns(inspector, table_name: str) -> set[str]:
    return {item["name"] for item in inspector.get_columns(table_name)}


def _repair_ambiguous_0033(bind) -> None:
    """Repair DBs where the old duplicate 0033 meant the unlisted migration."""
    inspector = sa.inspect(bind)
    if "submission_files" not in inspector.get_table_names():
        raise RuntimeError("Brak wymaganej tabeli submission_files przed migracją 20260813_0034.")
    existing = _columns(inspector, "submission_files")
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


def upgrade() -> None:
    bind = op.get_bind()
    _repair_ambiguous_0033(bind)
    inspector = sa.inspect(bind)
    if "forms" not in inspector.get_table_names():
        raise RuntimeError("Brak wymaganej tabeli forms przed migracją 20260813_0034.")
    existing = _columns(inspector, "forms")
    with op.batch_alter_table("forms") as batch:
        if "is_listed" not in existing:
            batch.add_column(sa.Column("is_listed", sa.Boolean(), nullable=False, server_default=sa.true()))
        if "share_token_hash" not in existing:
            batch.add_column(sa.Column("share_token_hash", sa.String(length=64), nullable=False, server_default=""))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "forms" not in inspector.get_table_names():
        return
    existing = _columns(inspector, "forms")
    with op.batch_alter_table("forms") as batch:
        if "share_token_hash" in existing:
            batch.drop_column("share_token_hash")
        if "is_listed" in existing:
            batch.drop_column("is_listed")
