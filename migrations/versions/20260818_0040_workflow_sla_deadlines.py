"""add workflow SLA deadlines, calendars and idempotent reminders

Revision ID: 20260818_0040
Revises: 20260818_0039
Create Date: 2026-08-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260818_0040"
down_revision = "20260818_0039"
branch_labels = None
depends_on = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    missing = {"form_versions", "form_submissions", "users", "email_logs"} - _tables()
    if missing:
        raise RuntimeError(f"Missing prerequisite tables before 20260818_0040: {', '.join(sorted(missing))}")

    if "business_calendars" not in _tables():
        op.create_table(
            "business_calendars",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("timezone_name", sa.String(64), nullable=False, server_default="Europe/Warsaw"),
            sa.Column("weekend_days", _json_type(), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    if "business_calendar_holidays" not in _tables():
        op.create_table(
            "business_calendar_holidays",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("business_calendar_id", sa.Integer(), sa.ForeignKey("business_calendars.id", ondelete="CASCADE"), nullable=False),
            sa.Column("holiday_date", sa.Date(), nullable=False),
            sa.Column("name", sa.String(255), nullable=False, server_default=""),
            sa.UniqueConstraint("business_calendar_id", "holiday_date", name="uq_business_calendar_holiday"),
        )

    if "workflow_step_sla_definitions" not in _tables():
        op.create_table(
            "workflow_step_sla_definitions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("form_version_id", sa.Integer(), sa.ForeignKey("form_versions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("step_key", sa.String(128), nullable=False),
            sa.Column("deadline_value", sa.Integer(), nullable=False),
            sa.Column("deadline_unit", sa.String(32), nullable=False),
            sa.Column("business_calendar_id", sa.Integer(), sa.ForeignKey("business_calendars.id", ondelete="SET NULL"), nullable=True),
            sa.Column("actor_type", sa.String(32), nullable=False, server_default="office"),
            sa.Column("reminders_json", _json_type(), nullable=False),
            sa.Column("escalation_json", _json_type(), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint("deadline_unit IN ('hours', 'calendar_days', 'business_days')", name="ck_workflow_step_sla_unit"),
            sa.UniqueConstraint("form_version_id", "step_key", name="uq_workflow_step_sla_version_step"),
        )

    if "submission_step_deadlines" not in _tables():
        op.create_table(
            "submission_step_deadlines",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("submission_id", sa.Integer(), sa.ForeignKey("form_submissions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("sla_definition_id", sa.Integer(), sa.ForeignKey("workflow_step_sla_definitions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("workflow_step", sa.String(128), nullable=False),
            sa.Column("actor_type", sa.String(32), nullable=False, server_default="office"),
            sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="active"),
            sa.Column("sla_definition_snapshot", _json_type(), nullable=False),
            sa.CheckConstraint("status IN ('active', 'completed', 'cancelled')", name="ck_submission_step_deadline_status"),
        )
        op.create_index("ix_submission_step_deadlines_submission_status", "submission_step_deadlines", ["submission_id", "status"])
        op.create_index("ix_submission_step_deadlines_due_status", "submission_step_deadlines", ["due_at", "status"])

    if "submission_deadline_notifications" not in _tables():
        op.create_table(
            "submission_deadline_notifications",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("submission_step_deadline_id", sa.Integer(), sa.ForeignKey("submission_step_deadlines.id", ondelete="CASCADE"), nullable=False),
            sa.Column("reminder_key", sa.String(128), nullable=False),
            sa.Column("recipient_email", sa.String(255), nullable=False),
            sa.Column("recipient_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("notification_type", sa.String(64), nullable=False),
            sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="claimed"),
            sa.Column("email_log_id", sa.Integer(), sa.ForeignKey("email_logs.id", ondelete="SET NULL"), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
            sa.UniqueConstraint(
                "submission_step_deadline_id", "reminder_key", "recipient_email", "notification_type",
                name="uq_submission_deadline_notification_delivery",
            ),
        )
        op.create_index(
            "ix_submission_deadline_notifications_status_scheduled",
            "submission_deadline_notifications", ["status", "scheduled_for"],
        )


def downgrade() -> None:
    for table in (
        "submission_deadline_notifications",
        "submission_step_deadlines",
        "workflow_step_sla_definitions",
        "business_calendar_holidays",
        "business_calendars",
    ):
        if table in _tables():
            op.drop_table(table)
