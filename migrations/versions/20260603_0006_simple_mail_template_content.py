"""simple mail template content fields

Revision ID: 20260603_0006
Revises: 20260603_0005
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa


revision = "20260603_0006"
down_revision = "20260603_0005"
branch_labels = None
depends_on = None


def _table_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _table_columns(table_name):
        op.add_column(table_name, column)


def upgrade() -> None:
    _add_column_if_missing("mail_templates", sa.Column("content_html", sa.Text(), nullable=False, server_default=""))
    _add_column_if_missing("mail_templates", sa.Column("content_text", sa.Text(), nullable=False, server_default=""))
    _add_column_if_missing("mail_templates", sa.Column("use_platform_layout", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    for column_name in ["use_platform_layout", "content_text", "content_html"]:
        if column_name in _table_columns("mail_templates"):
            op.drop_column("mail_templates", column_name)
