"""repair missing site footer social_show_labels column

Revision ID: 20260721_0022
Revises: 20260721_0021
"""

from alembic import op
import sqlalchemy as sa


revision = "20260721_0022"
down_revision = "20260721_0021"
branch_labels = None
depends_on = None


def _column_names() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "site_footers" not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns("site_footers")}


def upgrade() -> None:
    columns = _column_names()
    if not columns:
        return
    if "social_show_labels" not in columns:
        op.add_column(
            "site_footers",
            sa.Column(
                "social_show_labels",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )

    site_footers = sa.table(
        "site_footers",
        sa.column("social_show_labels", sa.Boolean()),
    )
    op.execute(
        site_footers.update()
        .where(site_footers.c.social_show_labels.is_(None))
        .values(social_show_labels=False)
    )


def downgrade() -> None:
    if "social_show_labels" in _column_names():
        op.drop_column("site_footers", "social_show_labels")
