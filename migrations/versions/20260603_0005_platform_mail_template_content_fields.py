"""platform mail template content fields

Revision ID: 20260603_0005
Revises: 20260603_0004
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa


revision = "20260603_0005"
down_revision = "20260603_0004"
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
    _add_column_if_missing("mail_templates", sa.Column("mail_type", sa.String(64), nullable=False, server_default="html_text"))
    _add_column_if_missing("mail_templates", sa.Column("content_title", sa.String(500), nullable=False, server_default=""))
    _add_column_if_missing("mail_templates", sa.Column("content_intro", sa.Text(), nullable=False, server_default=""))
    _add_column_if_missing("mail_templates", sa.Column("instruction_html", sa.Text(), nullable=False, server_default=""))
    _add_column_if_missing("mail_templates", sa.Column("instruction_text", sa.Text(), nullable=False, server_default=""))
    _add_column_if_missing("mail_templates", sa.Column("footer_note", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    for column_name in [
        "footer_note",
        "instruction_text",
        "instruction_html",
        "content_intro",
        "content_title",
        "mail_type",
    ]:
        if column_name in _table_columns("mail_templates"):
            op.drop_column("mail_templates", column_name)
