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


def _columns(table_name: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    if "show_process_status" not in _columns("mail_templates"):
        with op.batch_alter_table("mail_templates") as batch:
            batch.add_column(sa.Column("show_process_status", sa.Boolean(), nullable=False, server_default=sa.true()))
    email_log_columns = _columns("email_logs")
    with op.batch_alter_table("email_logs") as batch:
        if "html_body" not in email_log_columns:
            batch.add_column(sa.Column("html_body", sa.Text(), nullable=False, server_default=""))
        if "text_body" not in email_log_columns:
            batch.add_column(sa.Column("text_body", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    email_log_columns = _columns("email_logs")
    with op.batch_alter_table("email_logs") as batch:
        if "text_body" in email_log_columns:
            batch.drop_column("text_body")
        if "html_body" in email_log_columns:
            batch.drop_column("html_body")
    if "show_process_status" in _columns("mail_templates"):
        with op.batch_alter_table("mail_templates") as batch:
            batch.drop_column("show_process_status")
