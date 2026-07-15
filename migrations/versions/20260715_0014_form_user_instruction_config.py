"""Add configurable staged instructions to forms.

Revision ID: 20260715_0014
Revises: 20260714_0013
Create Date: 2026-07-15
"""

from alembic import op
import sqlalchemy as sa


revision = "20260715_0014"
down_revision = "20260714_0013"
branch_labels = None
depends_on = None


def _table_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if "user_instruction_config" in _table_columns("forms"):
        return
    op.add_column("forms", sa.Column("user_instruction_config", sa.JSON(), nullable=True))
    op.execute(
        sa.text("UPDATE forms SET user_instruction_config = :empty_config WHERE user_instruction_config IS NULL")
        .bindparams(empty_config="{}")
    )
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("forms") as batch_op:
            batch_op.alter_column("user_instruction_config", existing_type=sa.JSON(), nullable=False)
    else:
        op.alter_column("forms", "user_instruction_config", existing_type=sa.JSON(), nullable=False)


def downgrade() -> None:
    if "user_instruction_config" in _table_columns("forms"):
        op.drop_column("forms", "user_instruction_config")
