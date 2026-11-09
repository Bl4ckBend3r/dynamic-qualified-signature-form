"""encrypted form share tokens

Revision ID: b0be20605ea7
Revises: 4aec5ce4f5fa
Create Date: 2026-11-09 09:54:29.061750
"""

from alembic import op
import sqlalchemy as sa


revision = 'b0be20605ea7'
down_revision = '4aec5ce4f5fa'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("forms") as batch:
        batch.add_column(
            sa.Column("share_token_encrypted", sa.Text(), nullable=True)
        )


def downgrade():
    with op.batch_alter_table("forms") as batch:
        batch.drop_column("share_token_encrypted")