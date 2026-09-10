"""extend mail footer configuration

Revision ID: 20260717_0017
Revises: 20260715_0016
"""

from alembic import op
import sqlalchemy as sa


revision = "20260717_0017"
down_revision = "20260715_0016"
branch_labels = None
depends_on = None


def _column_names() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "mail_footers" not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns("mail_footers")}


def _add_column_if_missing(column: sa.Column) -> None:
    if column.name not in _column_names():
        op.add_column("mail_footers", column)


def upgrade() -> None:
    if "mail_footers" not in sa.inspect(op.get_bind()).get_table_names():
        return

    _add_column_if_missing(
        sa.Column(
            "logo_alignment",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'left'"),
        )
    )
    _add_column_if_missing(sa.Column("contact_html", sa.Text(), nullable=True))
    _add_column_if_missing(sa.Column("links", sa.JSON(), nullable=True))
    _add_column_if_missing(sa.Column("legal_text", sa.Text(), nullable=True))
    _add_column_if_missing(
        sa.Column("use_global", sa.Boolean(), nullable=False, server_default=sa.false())
    )

    bind = op.get_bind()
    footer = sa.table(
        "mail_footers",
        sa.column("logo_alignment", sa.String(length=20)),
    )
    bind.execute(
        footer.update()
        .where(footer.c.logo_alignment.is_(None))
        .values(logo_alignment="left")
    )

    # PostgreSQL and MySQL/MariaDB can safely tighten a manually-added nullable
    # column after the backfill. SQLite keeps the NOT NULL declaration used when
    # this migration creates the column and requires no table rebuild.
    if bind.dialect.name in {"postgresql", "mysql", "mariadb"}:
        op.alter_column(
            "mail_footers",
            "logo_alignment",
            existing_type=sa.String(length=20),
            nullable=False,
            server_default=sa.text("'left'"),
        )


def downgrade() -> None:
    if "mail_footers" not in sa.inspect(op.get_bind()).get_table_names():
        return
    for column_name in (
        "use_global",
        "legal_text",
        "links",
        "contact_html",
        "logo_alignment",
    ):
        if column_name in _column_names():
            op.drop_column("mail_footers", column_name)
