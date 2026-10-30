"""ensure the complete extended application schema

Revision ID: 20260727_0024
Revises: 20260727_0023
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260727_0024"
down_revision = "20260727_0023"
branch_labels = None
depends_on = None


def _json_type():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return postgresql.JSONB()
    return sa.JSON()


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _add_columns(table_name: str, columns: tuple[sa.Column, ...]) -> None:
    existing = _column_names(table_name)
    if not existing:
        return
    for column in columns:
        if column.name not in existing:
            op.add_column(table_name, column)
            existing.add(column.name)


def _backfill_defaults(table_name: str, values: dict[str, object]) -> None:
    existing = _column_names(table_name)
    available = {name: value for name, value in values.items() if name in existing}
    if not available:
        return
    table = sa.table(
        table_name,
        *[
            sa.column(name, sa.Boolean() if isinstance(value, bool) else sa.String())
            for name, value in available.items()
        ],
    )
    for name, value in available.items():
        column = table.c[name]
        op.execute(table.update().where(column.is_(None)).values({name: value}))


def _tighten_required_columns(
    table_name: str,
    columns: tuple[tuple[str, sa.types.TypeEngine, object], ...],
) -> None:
    bind = op.get_bind()
    if bind.dialect.name not in {"postgresql", "mysql", "mariadb"}:
        return
    existing = _column_names(table_name)
    for name, column_type, default in columns:
        if name not in existing:
            continue
        server_default = sa.false() if default is False else sa.true() if default is True else sa.text(f"'{default}'")
        op.alter_column(
            table_name,
            name,
            existing_type=column_type,
            nullable=False,
            server_default=server_default,
        )


def upgrade() -> None:
    _add_columns(
        "email_logs",
        (
            sa.Column("event_type", sa.String(100), nullable=True),
            sa.Column("error_type", sa.String(100), nullable=True),
            sa.Column("administrator_message", sa.Text(), nullable=True),
        ),
    )
    _add_columns(
        "forms",
        (
            sa.Column("user_instruction", sa.Text(), nullable=True),
            sa.Column("user_instruction_config", _json_type(), nullable=True),
        ),
    )
    _add_columns(
        "site_footers",
        (
            sa.Column("left_html", sa.Text(), nullable=True),
            sa.Column("right_html", sa.Text(), nullable=True),
            sa.Column("layout", sa.String(30), nullable=False, server_default="two_columns"),
            sa.Column("social_links", _json_type(), nullable=True),
            sa.Column("social_position", sa.String(30), nullable=False, server_default="left"),
            sa.Column("social_icon_style", sa.String(30), nullable=False, server_default="gold"),
            sa.Column("social_show_labels", sa.Boolean(), nullable=False, server_default=sa.false()),
        ),
    )
    _add_columns(
        "mail_footers",
        (
            sa.Column("logo_alignment", sa.String(20), nullable=False, server_default="left"),
            sa.Column("contact_html", sa.Text(), nullable=True),
            sa.Column("links", _json_type(), nullable=True),
            sa.Column("legal_text", sa.Text(), nullable=True),
            sa.Column("use_global", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("logo_width", sa.Integer(), nullable=True),
            sa.Column("logo_height", sa.Integer(), nullable=True),
            sa.Column("logo_position", sa.String(30), nullable=False, server_default="top"),
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        ),
    )

    _backfill_defaults(
        "site_footers",
        {
            "layout": "two_columns",
            "social_position": "left",
            "social_icon_style": "gold",
            "social_show_labels": False,
        },
    )
    _backfill_defaults(
        "mail_footers",
        {
            "logo_alignment": "left",
            "use_global": False,
            "logo_position": "top",
            "is_default": False,
            "is_active": True,
        },
    )
    _tighten_required_columns(
        "site_footers",
        (
            ("layout", sa.String(30), "two_columns"),
            ("social_position", sa.String(30), "left"),
            ("social_icon_style", sa.String(30), "gold"),
            ("social_show_labels", sa.Boolean(), False),
        ),
    )
    _tighten_required_columns(
        "mail_footers",
        (
            ("logo_alignment", sa.String(20), "left"),
            ("use_global", sa.Boolean(), False),
            ("logo_position", sa.String(30), "top"),
            ("is_default", sa.Boolean(), False),
            ("is_active", sa.Boolean(), True),
        ),
    )


def downgrade() -> None:
    # This is a repair migration. Dropping columns that may have existed before
    # Alembic would risk user data, so downgrade intentionally preserves them.
    pass
