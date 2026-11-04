"""Add optional general user instruction to forms.

Revision ID: 20260714_0013
Revises: 20260714_0012
Create Date: 2026-07-14
"""

from alembic import op
import sqlalchemy as sa


revision = "20260714_0013"
down_revision = "20260714_0012"
branch_labels = None
depends_on = None


def _table_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if "user_instruction" not in _table_columns("forms"):
        op.add_column("forms", sa.Column("user_instruction", sa.Text(), nullable=True))


def downgrade() -> None:
    if "user_instruction" in _table_columns("forms"):
        op.drop_column("forms", "user_instruction")
