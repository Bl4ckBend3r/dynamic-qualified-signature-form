"""store rendered correspondence and configure process status visibility

Revision ID: 20260804_0029
Revises: 20260730_0028
Create Date: 2026-08-04
"""

from alembic import op
import sqlalchemy as sa


revision = "20260804_0029"
down_revision = "20260730_0028"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    mail_template_columns = _column_names("mail_templates")
    if "show_process_status" not in mail_template_columns:
        op.add_column(
            "mail_templates",
            sa.Column(
                "show_process_status",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )

    email_log_columns = _column_names("email_logs")
    with op.batch_alter_table("email_logs") as batch:
        if "html_body" not in email_log_columns:
            batch.add_column(sa.Column("html_body", sa.Text(), nullable=False, server_default=""))
        if "text_body" not in email_log_columns:
            batch.add_column(sa.Column("text_body", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    email_log_columns = _column_names("email_logs")
    with op.batch_alter_table("email_logs") as batch:
        if "text_body" in email_log_columns:
            batch.drop_column("text_body")
        if "html_body" in email_log_columns:
            batch.drop_column("html_body")
    mail_template_columns = _column_names("mail_templates")
    if "show_process_status" in mail_template_columns:
        op.drop_column("mail_templates", "show_process_status")
