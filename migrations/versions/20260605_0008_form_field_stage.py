"""form field stage

Revision ID: 20260605_0008
Revises: 20260603_0007
Create Date: 2026-06-05
"""

from alembic import op
import sqlalchemy as sa


revision = "20260605_0008"
down_revision = "20260603_0007"
branch_labels = None
depends_on = None


def _table_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if "stage" not in _table_columns("form_fields"):
        op.add_column(
            "form_fields",
            sa.Column("stage", sa.String(64), nullable=False, server_default="initial_submission"),
        )
    if "additional_fields_completed" not in _table_columns("form_submissions"):
        op.add_column(
            "form_submissions",
            sa.Column("additional_fields_completed", sa.String(16), nullable=False, server_default=""),
        )


def downgrade() -> None:
    if "additional_fields_completed" in _table_columns("form_submissions"):
        op.drop_column("form_submissions", "additional_fields_completed")
    if "stage" in _table_columns("form_fields"):
        op.drop_column("form_fields", "stage")
