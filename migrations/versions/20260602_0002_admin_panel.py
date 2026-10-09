"""admin panel tables and dynamic submission data

Revision ID: 20260602_0002
Revises: 20260602_0001
Create Date: 2026-06-02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260602_0002"
down_revision = "20260602_0001"
branch_labels = None
depends_on = None


def _json_type():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return postgresql.JSONB()
    return sa.JSON()


def _text_column(name: str, length: int | None = None, default: str = ""):
    column_type = sa.String(length) if length else sa.Text()
    return sa.Column(name, column_type, nullable=False, server_default=default)


def _bool_column(name: str, default: bool = False):
    return sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.true() if default else sa.false())


def _table_columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    submission_columns = _table_columns("form_submissions")
    if "data_json" not in submission_columns:
        op.add_column(
            "form_submissions",
            sa.Column("data_json", _json_type(), nullable=False, server_default=sa.text("'{}'")),
        )
    if "updated_at" not in submission_columns:
        op.add_column(
            "form_submissions",
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        _text_column("role", 64, "form_manager"),
        _bool_column("is_active", True),
        _bool_column("is_blocked"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "forms",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        _text_column("title", 255),
        _text_column("description"),
        sa.Column("definition_json", _json_type(), nullable=False,server_default=sa.text("'{}'")),
        _bool_column("is_active", True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_forms_slug", "forms", ["slug"], unique=True)

    op.create_table(
        "form_fields",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("form_id", sa.Integer(), sa.ForeignKey("forms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        _text_column("label"),
        _text_column("type", 64, "text"),
        _bool_column("required"),
        sa.Column("options", _json_type(), nullable=False, server_default=sa.text("'[]'")),
        _text_column("default_value"),
        _text_column("section", 255),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_form_fields_form_id", "form_fields", ["form_id"])

    op.create_table(
        "form_permissions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("form_id", sa.Integer(), sa.ForeignKey("forms.id", ondelete="CASCADE"), nullable=False),
        _bool_column("can_manage", True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "form_id", name="uq_form_permissions_user_form"),
    )
    op.create_index("ix_form_permissions_user_id", "form_permissions", ["user_id"])
    op.create_index("ix_form_permissions_form_id", "form_permissions", ["form_id"])

    op.create_table(
        "mail_templates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("form_id", sa.Integer(), sa.ForeignKey("forms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        _text_column("template_type", 128, "manual"),
        _text_column("subject", 500),
        _text_column("html_body"),
        _bool_column("is_active", True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_mail_templates_form_id", "mail_templates", ["form_id"])

    op.create_table(
        "mail_footers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("form_id", sa.Integer(), sa.ForeignKey("forms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        _text_column("html_body"),
        _text_column("logo_path", 1024),
        _bool_column("is_default"),
        _bool_column("is_active", True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_mail_footers_form_id", "mail_footers", ["form_id"])

    op.create_table(
        "email_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("form_id", sa.Integer(), sa.ForeignKey("forms.id", ondelete="SET NULL"), nullable=True),
        sa.Column("submission_id", sa.Integer(), sa.ForeignKey("form_submissions.id", ondelete="SET NULL"), nullable=True),
        _text_column("public_submission_id", 64),
        _text_column("to_email", 255),
        _text_column("subject", 500),
        sa.Column("template_id", sa.Integer(), sa.ForeignKey("mail_templates.id", ondelete="SET NULL"), nullable=True),
        sa.Column("footer_id", sa.Integer(), sa.ForeignKey("mail_footers.id", ondelete="SET NULL"), nullable=True),
        _text_column("status", 64, "sent"),
        _text_column("error_message"),
        sa.Column("sent_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_email_logs_form_id", "email_logs", ["form_id"])
    op.create_index("ix_email_logs_submission_id", "email_logs", ["submission_id"])
    op.create_index("ix_email_logs_public_submission_id", "email_logs", ["public_submission_id"])


def downgrade() -> None:
    op.drop_index("ix_email_logs_public_submission_id", table_name="email_logs")
    op.drop_index("ix_email_logs_submission_id", table_name="email_logs")
    op.drop_index("ix_email_logs_form_id", table_name="email_logs")
    op.drop_table("email_logs")
    op.drop_index("ix_mail_footers_form_id", table_name="mail_footers")
    op.drop_table("mail_footers")
    op.drop_index("ix_mail_templates_form_id", table_name="mail_templates")
    op.drop_table("mail_templates")
    op.drop_index("ix_form_permissions_form_id", table_name="form_permissions")
    op.drop_index("ix_form_permissions_user_id", table_name="form_permissions")
    op.drop_table("form_permissions")
    op.drop_index("ix_form_fields_form_id", table_name="form_fields")
    op.drop_table("form_fields")
    op.drop_index("ix_forms_slug", table_name="forms")
    op.drop_table("forms")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    submission_columns = _table_columns("form_submissions")
    if "updated_at" in submission_columns:
        op.drop_column("form_submissions", "updated_at")
    if "data_json" in submission_columns:
        op.drop_column("form_submissions", "data_json")
