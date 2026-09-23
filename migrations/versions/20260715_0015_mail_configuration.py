"""Add platform and per-form mail configuration.

Revision ID: 20260715_0015
Revises: 20260715_0014
Create Date: 2026-07-15
"""

from alembic import op
import sqlalchemy as sa


revision = "20260715_0015"
down_revision = "20260715_0014"
branch_labels = None
depends_on = None


def _table_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    form_columns = _table_columns("forms")
    if "mail_mode" not in form_columns:
        op.add_column("forms", sa.Column("mail_mode", sa.String(length=32), nullable=False, server_default="system"))
    if "smtp_config" not in form_columns:
        op.add_column("forms", sa.Column("smtp_config", sa.JSON(), nullable=True))
    if "smtp_password_encrypted" not in form_columns:
        op.add_column("forms", sa.Column("smtp_password_encrypted", sa.Text(), nullable=True))

    tables = _tables()
    if "system_mail_settings" not in tables:
        op.create_table(
            "system_mail_settings",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("smtp_config", sa.JSON(), nullable=True),
            sa.Column("smtp_password_encrypted", sa.Text(), nullable=True),
            sa.Column("layout_config", sa.JSON(), nullable=True),
            sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
    if "platform_mail_templates" not in tables:
        op.create_table(
            "platform_mail_templates",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("template_type", sa.String(length=128), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("subject", sa.String(length=500), nullable=False, server_default=""),
            sa.Column("html_body", sa.Text(), nullable=False, server_default=""),
            sa.Column("text_body", sa.Text(), nullable=False, server_default=""),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("template_type", name="uq_platform_mail_templates_type"),
        )
        platform_templates = sa.table(
            "platform_mail_templates",
            sa.column("template_type", sa.String()),
            sa.column("name", sa.String()),
            sa.column("subject", sa.String()),
            sa.column("html_body", sa.Text()),
            sa.column("text_body", sa.Text()),
            sa.column("is_active", sa.Boolean()),
        )
        op.bulk_insert(
            platform_templates,
            [{
                "template_type": "submission_received",
                "name": "Potwierdzenie otrzymania zgłoszenia",
                "subject": "",
                "html_body": "",
                "text_body": "",
                "is_active": False,
            }],
        )

    if "mail_templates" in tables and "trigger_event" in _table_columns("mail_templates"):
        op.execute(
            sa.text(
                "UPDATE mail_templates SET is_active = :inactive "
                "WHERE trigger_event <> '' AND trigger_event NOT IN ('manual', 'manual_bulk')"
            ).bindparams(inactive=False)
        )


def downgrade() -> None:
    tables = _tables()
    if "platform_mail_templates" in tables:
        op.drop_table("platform_mail_templates")
    if "system_mail_settings" in tables:
        op.drop_table("system_mail_settings")
    form_columns = _table_columns("forms")
    if "smtp_password_encrypted" in form_columns:
        op.drop_column("forms", "smtp_password_encrypted")
    if "smtp_config" in form_columns:
        op.drop_column("forms", "smtp_config")
    if "mail_mode" in form_columns:
        op.drop_column("forms", "mail_mode")
