"""add versioned manual verification checklists

Revision ID: 20260818_0038
Revises: 20260813_0037
Create Date: 2026-08-18
"""

from alembic import op
import sqlalchemy as sa


revision = "20260818_0038"
down_revision = "20260813_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    required = {"form_versions", "form_submissions", "submission_files", "users", "form_permissions"}
    missing = sorted(required - tables)
    if missing:
        raise RuntimeError(f"Missing prerequisite tables before 20260818_0038: {', '.join(missing)}")

    permission_columns = {column["name"] for column in inspector.get_columns("form_permissions")}
    with op.batch_alter_table("form_permissions") as batch:
        if "can_make_decision" not in permission_columns:
            batch.add_column(sa.Column("can_make_decision", sa.Boolean(), nullable=False, server_default=sa.true()))
        if "can_view_sensitive_data" not in permission_columns:
            batch.add_column(sa.Column("can_view_sensitive_data", sa.Boolean(), nullable=False, server_default=sa.true()))

    if "verification_checklist_definitions" not in tables:
        op.create_table(
            "verification_checklist_definitions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("form_version_id", sa.Integer(), sa.ForeignKey("form_versions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("workflow_step", sa.String(128), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
        op.create_index("ix_verification_checklists_version_step", "verification_checklist_definitions", ["form_version_id", "workflow_step"])

    if "verification_checklist_item_definitions" not in tables:
        op.create_table(
            "verification_checklist_item_definitions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("checklist_id", sa.Integer(), sa.ForeignKey("verification_checklist_definitions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("key", sa.String(128), nullable=False),
            sa.Column("label", sa.String(500), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("blocking", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("document_required", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("allow_not_applicable", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("sensitive", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("rules_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.UniqueConstraint("checklist_id", "key", name="uq_verification_checklist_item_key"),
        )

    if "submission_checklist_item_results" not in tables:
        op.create_table(
            "submission_checklist_item_results",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("submission_id", sa.Integer(), sa.ForeignKey("form_submissions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("checklist_item_definition_id", sa.Integer(), sa.ForeignKey("verification_checklist_item_definitions.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("result", sa.String(32), nullable=False, server_default="pending"),
            sa.Column("comment", sa.Text(), nullable=False, server_default=""),
            sa.Column("officer_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("submission_id", "checklist_item_definition_id", name="uq_submission_checklist_item_result"),
            sa.CheckConstraint("result IN ('yes', 'no', 'not_applicable', 'pending')", name="ck_submission_checklist_item_result"),
        )
        op.create_index("ix_submission_checklist_results_submission_result", "submission_checklist_item_results", ["submission_id", "result"])

    if "submission_checklist_evidence" not in tables:
        op.create_table(
            "submission_checklist_evidence",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("result_id", sa.Integer(), sa.ForeignKey("submission_checklist_item_results.id", ondelete="CASCADE"), nullable=False),
            sa.Column("submission_file_id", sa.Integer(), sa.ForeignKey("submission_files.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.UniqueConstraint("result_id", "submission_file_id", name="uq_submission_checklist_evidence"),
        )

    if "submission_checklist_result_history" not in tables:
        op.create_table(
            "submission_checklist_result_history",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("result_id", sa.Integer(), sa.ForeignKey("submission_checklist_item_results.id", ondelete="CASCADE"), nullable=False),
            sa.Column("previous_result", sa.String(32), nullable=False),
            sa.Column("new_result", sa.String(32), nullable=False),
            sa.Column("previous_comment", sa.Text(), nullable=False, server_default=""),
            sa.Column("new_comment", sa.Text(), nullable=False, server_default=""),
            sa.Column("officer_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_submission_checklist_history_result_changed", "submission_checklist_result_history", ["result_id", "changed_at"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    for table in (
        "submission_checklist_result_history",
        "submission_checklist_evidence",
        "submission_checklist_item_results",
        "verification_checklist_item_definitions",
        "verification_checklist_definitions",
    ):
        if table in tables:
            op.drop_table(table)
    if "form_permissions" in tables:
        columns = {column["name"] for column in sa.inspect(bind).get_columns("form_permissions")}
        with op.batch_alter_table("form_permissions") as batch:
            for column in ("can_view_sensitive_data", "can_make_decision"):
                if column in columns:
                    batch.drop_column(column)
