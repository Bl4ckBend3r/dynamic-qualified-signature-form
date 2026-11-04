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


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    columns = _column_names("email_logs")
    if not columns:
        return
    additions = (
        sa.Column("event_type", sa.String(100), nullable=True),
        sa.Column("error_type", sa.String(100), nullable=True),
        sa.Column("administrator_message", sa.Text(), nullable=True),
    )
    for column in additions:
        if column.name not in columns:
            op.add_column("email_logs", column)
            columns.add(column.name)
    if "ix_email_logs_event_type" not in _index_names("email_logs"):
        op.create_index("ix_email_logs_event_type", "email_logs", ["event_type"])


def downgrade() -> None:
    if "ix_email_logs_event_type" in _index_names("email_logs"):
        op.drop_index("ix_email_logs_event_type", table_name="email_logs")
    columns = _column_names("email_logs")
    for column_name in ("administrator_message", "error_type", "event_type"):
        if column_name in columns:
            op.drop_column("email_logs", column_name)
