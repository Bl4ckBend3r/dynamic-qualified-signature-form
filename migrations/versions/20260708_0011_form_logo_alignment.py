"""Form logo alignment

Revision ID: 20260708_0011
Revises: 20260707_0010
Create Date: 2026-07-08
"""

from alembic import op
import sqlalchemy as sa


revision = "20260708_0011"
down_revision = "20260707_0010"
branch_labels = None
depends_on = None


def _table_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if "logo_alignment" not in _table_columns("forms"):
        op.add_column(
            "forms",
            sa.Column("logo_alignment", sa.String(16), nullable=False, server_default="left"),
        )


def downgrade() -> None:
    if "logo_alignment" in _table_columns("forms"):
        op.drop_column("forms", "logo_alignment")
