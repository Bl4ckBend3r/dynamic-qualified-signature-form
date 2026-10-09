"""add columns and social links to public site footer

Revision ID: 20260721_0020
Revises: 20260720_0019
"""

from alembic import op
import sqlalchemy as sa


revision = "20260721_0020"
down_revision = "20260720_0019"
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

    additions = (
        ("left_html", sa.Text(), True, None),
        ("right_html", sa.Text(), True, None),
        ("layout", sa.String(length=30), False, "two_columns"),
        ("social_links", sa.JSON(), True, None),
        ("social_position", sa.String(length=30), False, "left"),
        ("social_icon_style", sa.String(length=30), False, "gold"),
    )
    for name, column_type, nullable, default in additions:
        if name in columns:
            continue
        kwargs = {"nullable": nullable}
        if default is not None:
            kwargs["server_default"] = default
        op.add_column("site_footers", sa.Column(name, column_type, **kwargs))

    # Explicit backfill also covers databases where a column was added manually
    # without its intended default.
    op.execute(
        sa.text(
            "UPDATE site_footers SET layout = COALESCE(layout, 'two_columns'), "
            "social_position = COALESCE(social_position, 'left'), "
            "social_icon_style = COALESCE(social_icon_style, 'gold')"
        )
    )


def downgrade() -> None:
    columns = _column_names()
    for name in (
        "social_icon_style",
        "social_position",
        "social_links",
        "layout",
        "right_html",
        "left_html",
    ):
        if name in columns:
            op.drop_column("site_footers", name)
