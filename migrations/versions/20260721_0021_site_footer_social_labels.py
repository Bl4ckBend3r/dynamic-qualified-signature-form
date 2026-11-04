"""add optional visible labels to site footer social links

Revision ID: 20260721_0021
Revises: 20260721_0020
"""

from alembic import op
import sqlalchemy as sa


revision = "20260721_0021"
down_revision = "20260721_0020"
branch_labels = None
depends_on = None


def _column_names() -> set[str]:
    bind = op.get_bind()
    if "site_footers" not in sa.inspect(bind).get_table_names():
        return set()
    return {column["name"] for column in sa.inspect(bind).get_columns("site_footers")}


def upgrade() -> None:
    columns = _column_names()
    if not columns:
        return
    if "social_show_labels" not in columns:
        op.add_column(
            "site_footers",
            sa.Column("social_show_labels", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    op.execute(sa.text("UPDATE site_footers SET social_show_labels = false WHERE social_show_labels IS NULL"))


def downgrade() -> None:
    if "social_show_labels" in _column_names():
        op.drop_column("site_footers", "social_show_labels")
