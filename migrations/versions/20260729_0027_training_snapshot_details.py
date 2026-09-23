"""add durable training selection details snapshot

Revision ID: 20260729_0027
Revises: 20260729_0026
"""

from alembic import op
import sqlalchemy as sa


revision = "20260729_0027"
down_revision = "20260729_0026"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {
        column["name"]
        for column in inspector.get_columns(table_name)
    }


def _json_type():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        from sqlalchemy.dialects import postgresql

        return postgresql.JSONB()
    return sa.JSON()


def upgrade() -> None:
    columns = _column_names("submission_trainings")
    if columns and "training_snapshot" not in columns:
        op.add_column(
            "submission_trainings",
            sa.Column("training_snapshot", _json_type(), nullable=True),
        )


def downgrade() -> None:
    if "training_snapshot" in _column_names("submission_trainings"):
        op.drop_column("submission_trainings", "training_snapshot")
