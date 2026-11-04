"""mail footer logo relation

Revision ID: 20260603_0007
Revises: 20260603_0006
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa


revision = "20260603_0007"
down_revision = "20260603_0006"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _table_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _foreign_key_columns(table_name: str) -> set[tuple[str, ...]]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {tuple(fk.get("constrained_columns", [])) for fk in inspector.get_foreign_keys(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _table_columns(table_name):
        op.add_column(table_name, column)


def upgrade() -> None:
    tables = _table_names()

    if "logos" not in tables:
        op.create_table(
            "logos",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("filename", sa.String(512), nullable=False),
            sa.Column("storage_path", sa.Text(), nullable=False),
            sa.Column("mime_type", sa.String(255), nullable=False, server_default=""),
            sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("checksum_sha256", sa.String(64), nullable=False, server_default=""),
            sa.Column("uploaded_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
    else:
        _add_column_if_missing("logos", sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"))
        _add_column_if_missing("logos", sa.Column("checksum_sha256", sa.String(64), nullable=False, server_default=""))
        _add_column_if_missing("logos", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))

    if "mail_footers" in _table_names():
        _add_column_if_missing("mail_footers", sa.Column("logo_id", sa.Integer(), nullable=True))
        if "ix_mail_footers_logo_id" not in _index_names("mail_footers"):
            op.create_index("ix_mail_footers_logo_id", "mail_footers", ["logo_id"])
        if "logo_id" in _table_columns("mail_footers") and ("logo_id",) not in _foreign_key_columns("mail_footers"):
            op.create_foreign_key(
                "fk_mail_footers_logo_id_logos",
                "mail_footers",
                "logos",
                ["logo_id"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade() -> None:
    if "mail_footers" in _table_names() and "logo_id" in _table_columns("mail_footers"):
        try:
            op.drop_constraint("fk_mail_footers_logo_id_logos", "mail_footers", type_="foreignkey")
        except Exception:
            pass
        if "ix_mail_footers_logo_id" in _index_names("mail_footers"):
            op.drop_index("ix_mail_footers_logo_id", table_name="mail_footers")
        op.drop_column("mail_footers", "logo_id")

    if "logos" in _table_names():
        for column_name in ["updated_at", "checksum_sha256", "size_bytes"]:
            if column_name in _table_columns("logos"):
                op.drop_column("logos", column_name)
