"""repair public form columns after noop head

Revision ID: 20260603_0004
Revises: 47ad644d2443
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa


revision = "20260603_0004"
down_revision = "47ad644d2443"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _table_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _table_columns(table_name):
        op.add_column(table_name, column)


def _has_fk(table_name: str, column_name: str) -> bool:
    for fk in sa.inspect(op.get_bind()).get_foreign_keys(table_name):
        if column_name in fk.get("constrained_columns", []):
            return True
    return False


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
            sa.Column("uploaded_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )

    _add_column_if_missing("forms", sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.true()))
    _add_column_if_missing("forms", sa.Column("label_text", sa.String(255), nullable=False, server_default=""))
    _add_column_if_missing("forms", sa.Column("label_variant", sa.String(64), nullable=False, server_default="project"))
    _add_column_if_missing("forms", sa.Column("label_color", sa.String(64), nullable=False, server_default="#b38d45"))
    _add_column_if_missing("forms", sa.Column("label_background", sa.String(64), nullable=False, server_default="#f7f3ec"))
    _add_column_if_missing("forms", sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"))
    _add_column_if_missing("forms", sa.Column("logo_id", sa.Integer(), nullable=True))
    if "logo_id" in _table_columns("forms") and not _has_fk("forms", "logo_id"):
        op.create_foreign_key("fk_forms_logo_id_logos", "forms", "logos", ["logo_id"], ["id"], ondelete="SET NULL")

    _add_column_if_missing("form_fields", sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()))

    _add_column_if_missing("mail_templates", sa.Column("text_body", sa.Text(), nullable=False, server_default=""))
    _add_column_if_missing("mail_templates", sa.Column("trigger_event", sa.String(128), nullable=False, server_default=""))
    _add_column_if_missing("mail_templates", sa.Column("trigger_status", sa.String(128), nullable=False, server_default=""))
    _add_column_if_missing("mail_templates", sa.Column("trigger_decision", sa.String(64), nullable=False, server_default=""))
    _add_column_if_missing("mail_templates", sa.Column("is_default_for_status", sa.Boolean(), nullable=False, server_default=sa.false()))

    if "mail_template_assets" not in _table_names():
        op.create_table(
            "mail_template_assets",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("template_id", sa.Integer(), sa.ForeignKey("mail_templates.id", ondelete="CASCADE"), nullable=False),
            sa.Column("filename", sa.String(512), nullable=False),
            sa.Column("storage_path", sa.Text(), nullable=False, server_default=""),
            sa.Column("mime_type", sa.String(255), nullable=False, server_default=""),
            sa.Column("content", sa.LargeBinary(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        op.create_index("ix_mail_template_assets_template_id", "mail_template_assets", ["template_id"])


def downgrade() -> None:
    # This repair migration is intentionally not destructive. The previous
    # migration contains the reversible schema definition.
    pass
