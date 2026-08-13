"""add submission ownership, assignment audit and round-robin state

Revision ID: 20260813_0037
Revises: 20260813_0036
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa


revision = "20260813_0037"
down_revision = "20260813_0036"
branch_labels = None
depends_on = None


def _tables(inspector) -> set[str]:
    return set(inspector.get_table_names())


def _columns(inspector, table_name: str) -> set[str]:
    return {item["name"] for item in inspector.get_columns(table_name)}


def _indexes(inspector, table_name: str) -> set[str]:
    return {item["name"] for item in inspector.get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    required = {"form_submissions", "forms", "users", "form_permissions"}
    missing = sorted(required - _tables(inspector))
    if missing:
        raise RuntimeError(f"Missing prerequisite tables before 20260813_0037: {', '.join(missing)}")

    submission_columns = _columns(inspector, "form_submissions")
    with op.batch_alter_table("form_submissions") as batch:
        if "assigned_to_user_id" not in submission_columns:
            batch.add_column(sa.Column("assigned_to_user_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(
                "fk_form_submissions_assigned_to_user_id", "users", ["assigned_to_user_id"], ["id"], ondelete="RESTRICT"
            )
        if "assigned_at" not in submission_columns:
            batch.add_column(sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True))
        if "assigned_by_user_id" not in submission_columns:
            batch.add_column(sa.Column("assigned_by_user_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(
                "fk_form_submissions_assigned_by_user_id", "users", ["assigned_by_user_id"], ["id"], ondelete="RESTRICT"
            )
        if "priority" not in submission_columns:
            batch.add_column(sa.Column("priority", sa.String(16), nullable=False, server_default="normal"))
            batch.create_check_constraint(
                "ck_form_submissions_priority", "priority IN ('low', 'normal', 'high', 'urgent')"
            )
        if "due_at" not in submission_columns:
            batch.add_column(sa.Column("due_at", sa.DateTime(timezone=True), nullable=True))

    inspector = sa.inspect(bind)
    submission_indexes = _indexes(inspector, "form_submissions")
    for name, columns in (
        ("ix_form_submissions_assigned_to_user_id", ["assigned_to_user_id"]),
        ("ix_form_submissions_priority", ["priority"]),
        ("ix_form_submissions_due_at", ["due_at"]),
    ):
        if name not in submission_indexes:
            op.create_index(name, "form_submissions", columns)

    permission_columns = _columns(sa.inspect(bind), "form_permissions")
    with op.batch_alter_table("form_permissions") as batch:
        if "can_review" not in permission_columns:
            batch.add_column(sa.Column("can_review", sa.Boolean(), nullable=False, server_default=sa.true()))
        if "can_assign_submissions" not in permission_columns:
            batch.add_column(sa.Column("can_assign_submissions", sa.Boolean(), nullable=False, server_default=sa.false()))

    inspector = sa.inspect(bind)
    if "submission_assignment_history" not in _tables(inspector):
        op.create_table(
            "submission_assignment_history",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("submission_id", sa.Integer(), sa.ForeignKey("form_submissions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("assigned_to_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
            sa.Column("assigned_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
            sa.Column("previous_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
            sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("unassigned_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("source", sa.String(32), nullable=False, server_default="manual"),
            sa.CheckConstraint(
                "source IN ('manual', 'round_robin', 'workflow_rule', 'system')",
                name="ck_submission_assignment_history_source",
            ),
        )
        op.create_index("ix_submission_assignment_history_submission_id", "submission_assignment_history", ["submission_id"])
        op.create_index("ix_submission_assignment_history_assigned_at", "submission_assignment_history", ["assigned_at"])

    if "form_assignment_states" not in _tables(sa.inspect(bind)):
        op.create_table(
            "form_assignment_states",
            sa.Column("form_id", sa.Integer(), sa.ForeignKey("forms.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("last_assigned_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = _tables(inspector)
    if "form_assignment_states" in tables:
        op.drop_table("form_assignment_states")
    if "submission_assignment_history" in tables:
        op.drop_table("submission_assignment_history")

    if "form_permissions" in tables:
        permission_columns = _columns(sa.inspect(bind), "form_permissions")
        with op.batch_alter_table("form_permissions") as batch:
            for column in ("can_assign_submissions", "can_review"):
                if column in permission_columns:
                    batch.drop_column(column)

    if "form_submissions" in tables:
        submission_columns = _columns(sa.inspect(bind), "form_submissions")
        submission_indexes = _indexes(sa.inspect(bind), "form_submissions")
        with op.batch_alter_table("form_submissions") as batch:
            if "assigned_by_user_id" in submission_columns:
                batch.drop_constraint("fk_form_submissions_assigned_by_user_id", type_="foreignkey")
            if "assigned_to_user_id" in submission_columns:
                batch.drop_constraint("fk_form_submissions_assigned_to_user_id", type_="foreignkey")
            if "priority" in submission_columns:
                batch.drop_constraint("ck_form_submissions_priority", type_="check")
            for name in (
                "ix_form_submissions_due_at", "ix_form_submissions_priority", "ix_form_submissions_assigned_to_user_id"
            ):
                if name in submission_indexes:
                    batch.drop_index(name)
            for column in ("due_at", "priority", "assigned_by_user_id", "assigned_at", "assigned_to_user_id"):
                if column in submission_columns:
                    batch.drop_column(column)
