"""public forms, labels, mail triggers and logos

Revision ID: 20260603_0003
Revises: 20260602_0002
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa


revision = "20260603_0003"
down_revision = "20260602_0002"
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
    if "logo_id" in _table_columns("forms"):
        foreign_keys = {
            tuple(fk.get("constrained_columns", []))
            for fk in sa.inspect(op.get_bind()).get_foreign_keys("forms")
        }
        if ("logo_id",) not in foreign_keys:
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
    if "mail_template_assets" in _table_names():
        op.drop_index("ix_mail_template_assets_template_id", table_name="mail_template_assets")
        op.drop_table("mail_template_assets")

    for column_name in [
        "is_default_for_status",
        "trigger_decision",
        "trigger_status",
        "trigger_event",
        "text_body",
    ]:
        if column_name in _table_columns("mail_templates"):
            op.drop_column("mail_templates", column_name)

    if "active" in _table_columns("form_fields"):
        op.drop_column("form_fields", "active")

    if "logo_id" in _table_columns("forms"):
        try:
            op.drop_constraint("fk_forms_logo_id_logos", "forms", type_="foreignkey")
        except Exception:
            pass
    for column_name in [
        "logo_id",
        "sort_order",
        "label_background",
        "label_color",
        "label_variant",
        "label_text",
        "is_public",
    ]:
        if column_name in _table_columns("forms"):
            op.drop_column("forms", column_name)

    if "logos" in _table_names():
        op.drop_table("logos")
