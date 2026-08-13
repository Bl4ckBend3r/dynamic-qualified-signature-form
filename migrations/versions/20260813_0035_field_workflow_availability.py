"""align form field availability with workflow steps

Revision ID: 20260813_0035
Revises: 20260813_0034
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260813_0035"
down_revision = "20260813_0034"
branch_labels = None
depends_on = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade():
    op.add_column(
        "form_fields",
        sa.Column("availability_json", _json_type(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade():
    op.drop_column("form_fields", "availability_json")
