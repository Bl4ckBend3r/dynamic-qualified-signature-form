"""add mail footer logo size and position

Revision ID: 20260720_0018
Revises: 20260717_0017
"""

from alembic import op
import sqlalchemy as sa


revision = "20260720_0018"
down_revision = "20260717_0017"
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
    bind = op.get_bind()
    if "mail_footers" not in sa.inspect(bind).get_table_names():
        return

    _add_column_if_missing(sa.Column("logo_width", sa.Integer(), nullable=True))
    _add_column_if_missing(sa.Column("logo_height", sa.Integer(), nullable=True))
    _add_column_if_missing(
        sa.Column(
            "logo_position",
            sa.String(length=30),
            nullable=False,
            server_default=sa.text("'top'"),
        )
    )

    footer = sa.table(
        "mail_footers",
        sa.column("logo_position", sa.String(length=30)),
    )
    bind.execute(
        footer.update()
        .where(footer.c.logo_position.is_(None))
        .values(logo_position="top")
    )

    # A manually added column can still be nullable. PostgreSQL and
    # MySQL/MariaDB can tighten it safely after the backfill.
    if bind.dialect.name in {"postgresql", "mysql", "mariadb"}:
        op.alter_column(
            "mail_footers",
            "logo_position",
            existing_type=sa.String(length=30),
            nullable=False,
            server_default=sa.text("'top'"),
        )


def downgrade() -> None:
    if "mail_footers" not in sa.inspect(op.get_bind()).get_table_names():
        return
    for column_name in ("logo_position", "logo_height", "logo_width"):
        if column_name in _column_names():
            op.drop_column("mail_footers", column_name)
