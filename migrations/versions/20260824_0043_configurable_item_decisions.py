"""configurable decisions, per-item audit and project logo

Revision ID: 20260824_0043
Revises: 20260818_0042
Create Date: 2026-08-24
"""

from alembic import op
import sqlalchemy as sa


revision = "20260824_0043"
down_revision = "20260818_0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("submission_decisions") as batch:
        batch.alter_column("decision", existing_type=sa.String(64), type_=sa.String(128), existing_nullable=False)
        batch.add_column(sa.Column("decision_label", sa.String(255), nullable=False, server_default=""))
        batch.add_column(sa.Column("semantic_category", sa.String(32), nullable=False, server_default=""))
        batch.add_column(sa.Column("workflow_step", sa.String(128), nullable=False, server_default=""))
        batch.add_column(sa.Column("target_step", sa.String(128), nullable=False, server_default=""))
    with op.batch_alter_table("form_submissions") as batch:
        batch.alter_column("officer_decision", existing_type=sa.String(32), type_=sa.String(128), existing_nullable=False)
    op.create_table(
        "decision_type_definitions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("semantic_category", sa.String(32), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("semantic_category IN ('positive', 'negative', 'correction', 'neutral')", name="ck_decision_type_semantic_category"),
        sa.UniqueConstraint("code", name="uq_decision_type_definitions_code"),
    )
    op.create_index("ix_decision_type_definitions_code", "decision_type_definitions", ["code"])
    op.create_table(
        "repeatable_group_item_decisions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("submission_id", sa.Integer(), sa.ForeignKey("form_submissions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("group_key", sa.String(255), nullable=False),
        sa.Column("item_id", sa.String(36), nullable=False),
        sa.Column("decision_type_id", sa.Integer(), sa.ForeignKey("decision_type_definitions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decision_code", sa.String(128), nullable=False),
        sa.Column("decision_label", sa.String(255), nullable=False),
        sa.Column("semantic_category", sa.String(32), nullable=False),
        sa.Column("workflow_step", sa.String(128), nullable=False, server_default=""),
        sa.Column("target_step", sa.String(128), nullable=False, server_default=""),
        sa.Column("comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("decided_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_repeatable_item_decision_lookup", "repeatable_group_item_decisions", ["submission_id", "group_key", "item_id", "decided_at"])
    op.create_index("ix_repeatable_item_decision_code", "repeatable_group_item_decisions", ["decision_code"])
    with op.batch_alter_table("forms") as batch:
        batch.add_column(sa.Column("project_logo_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_forms_project_logo_id_logos", "logos", ["project_logo_id"], ["id"], ondelete="SET NULL")
    with op.batch_alter_table("email_logs") as batch:
        batch.add_column(sa.Column("repeatable_group_key", sa.String(255), nullable=False, server_default=""))
        batch.add_column(sa.Column("repeatable_item_id", sa.String(36), nullable=False, server_default=""))
        batch.add_column(sa.Column("item_decision_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_email_logs_item_decision_id", "repeatable_group_item_decisions", ["item_decision_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    with op.batch_alter_table("email_logs") as batch:
        batch.drop_constraint("fk_email_logs_item_decision_id", type_="foreignkey")
        batch.drop_column("item_decision_id")
        batch.drop_column("repeatable_item_id")
        batch.drop_column("repeatable_group_key")
    with op.batch_alter_table("forms") as batch:
        batch.drop_constraint("fk_forms_project_logo_id_logos", type_="foreignkey")
        batch.drop_column("project_logo_id")
    op.drop_index("ix_repeatable_item_decision_code", table_name="repeatable_group_item_decisions")
    op.drop_index("ix_repeatable_item_decision_lookup", table_name="repeatable_group_item_decisions")
    op.drop_table("repeatable_group_item_decisions")
    op.drop_index("ix_decision_type_definitions_code", table_name="decision_type_definitions")
    op.drop_table("decision_type_definitions")
    with op.batch_alter_table("submission_decisions") as batch:
        batch.drop_column("target_step")
        batch.drop_column("workflow_step")
        batch.drop_column("semantic_category")
        batch.drop_column("decision_label")
        batch.alter_column("decision", existing_type=sa.String(128), type_=sa.String(64), existing_nullable=False)
    with op.batch_alter_table("form_submissions") as batch:
        batch.alter_column("officer_decision", existing_type=sa.String(128), type_=sa.String(32), existing_nullable=False)
