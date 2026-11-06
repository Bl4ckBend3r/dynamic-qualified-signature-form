"""add soft delete columns to form submissions

Revision ID: 4aec5ce4f5fa
Revises: 20261103_0050
Create Date: 2026-11-04 09:49:32.947280
"""

from alembic import op
import sqlalchemy as sa


revision = '4aec5ce4f5fa'
down_revision = '20261103_0050'
branch_labels = None
depends_on = None



def upgrade():
    connection = op.get_bind()
    existing_columns = {
        column["name"]
        for column in sa.inspect(connection).get_columns(
            "form_submissions"
        )
    }

    if "deleted_at" not in existing_columns:
        op.add_column(
            "form_submissions",
            sa.Column(
                "deleted_at",
                sa.DateTime(),
                nullable=True,
            ),
        )

    if "deleted_by_user_id" not in existing_columns:
        op.add_column(
            "form_submissions",
            sa.Column(
                "deleted_by_user_id",
                sa.Integer(),
                nullable=True,
            ),
        )


def downgrade():
    op.drop_column(
        "form_submissions",
        "deleted_by_user_id",
    )
    op.drop_column(
        "form_submissions",
        "deleted_at",
    )
