"""Add configurable staged instructions to forms.

Revision ID: 20260715_0014
Revises: 20260714_0013
Create Date: 2026-07-15
"""

from alembic import op
import sqlalchemy as sa


revision = "20260715_0014"
down_revision = "20260714_0013"
branch_labels = None
depends_on = None


def _table_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _instruction_config_type():
    dialect_name = op.get_bind().dialect.name
    if dialect_name in {"postgresql", "mysql", "mariadb"}:
        return sa.JSON()
    return sa.Text()


def upgrade() -> None:
    if "user_instruction_config" in _table_columns("forms"):
        return
    op.add_column(
        "forms",
        sa.Column("user_instruction_config", _instruction_config_type(), nullable=True),
    )


def downgrade() -> None:
    if "user_instruction_config" in _table_columns("forms"):
        op.drop_column("forms", "user_instruction_config")
