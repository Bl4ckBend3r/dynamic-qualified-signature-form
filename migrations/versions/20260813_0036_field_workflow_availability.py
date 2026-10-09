"""align form field availability with workflow steps

Revision ID: 20260813_0036
Revises: 20260813_0035
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260813_0036"
down_revision = "20260813_0035"
branch_labels = None
depends_on = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "form_fields" not in inspector.get_table_names():
        raise RuntimeError("Missing prerequisite table form_fields before migration 20260813_0036.")
    columns = {item["name"] for item in inspector.get_columns("form_fields")}
    if "availability_json" not in columns:
        op.add_column(
            "form_fields",
            sa.Column("availability_json", _json_type(), nullable=False, server_default=sa.text("'[]'")),
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "form_fields" in inspector.get_table_names():
        columns = {item["name"] for item in inspector.get_columns("form_fields")}
        if "availability_json" in columns:
            op.drop_column("form_fields", "availability_json")
