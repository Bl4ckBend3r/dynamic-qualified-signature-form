"""add structured SMTP test diagnostics to email logs

Revision ID: 20260727_0023
Revises: 20260721_0022
"""

from alembic import op
import sqlalchemy as sa


revision = "20260727_0023"
down_revision = "20260721_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "email_logs",
        sa.Column("event_type", sa.String(64), nullable=False, server_default="email_delivery"),
    )
    op.add_column(
        "email_logs",
        sa.Column("error_type", sa.String(128), nullable=False, server_default=""),
    )
    op.add_column(
        "email_logs",
        sa.Column("administrator_message", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_email_logs_event_type", "email_logs", ["event_type"])


def downgrade() -> None:
    op.drop_index("ix_email_logs_event_type", table_name="email_logs")
    op.drop_column("email_logs", "administrator_message")
    op.drop_column("email_logs", "error_type")
    op.drop_column("email_logs", "event_type")
