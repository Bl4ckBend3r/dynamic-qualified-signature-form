"""standalone operational training management

Revision ID: 20260824_0044
Revises: 20260824_0043
Create Date: 2026-08-24
"""

from alembic import op
import sqlalchemy as sa


revision = "20260824_0044"
down_revision = "20260824_0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "training_participant_action_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("form_id", sa.Integer(), sa.ForeignKey("forms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("training_id", sa.String(255), nullable=False),
        sa.Column("submission_training_id", sa.Integer(), sa.ForeignKey("submission_trainings.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("previous_value", sa.String(128), nullable=False, server_default=""),
        sa.Column("new_value", sa.String(128), nullable=False, server_default=""),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_training_participant_history_training", "training_participant_action_history", ["submission_training_id", "created_at"])
    op.create_index("ix_training_action_history_scope", "training_participant_action_history", ["form_id", "training_id", "created_at"])

    op.create_table(
        "training_attendance_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("form_id", sa.Integer(), sa.ForeignKey("forms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("training_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.String(8), nullable=False, server_default=""),
        sa.Column("end_time", sa.String(8), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('draft', 'active', 'closed')", name="ck_training_attendance_sessions_status"),
    )
    op.create_index("ix_training_attendance_sessions_training", "training_attendance_sessions", ["form_id", "training_id", "session_date"])

    op.create_table(
        "training_attendance_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("training_attendance_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("submission_training_id", sa.Integer(), sa.ForeignKey("submission_trainings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("confirmation_method", sa.String(32), nullable=False, server_default=""),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('pending', 'present', 'absent', 'excused')", name="ck_training_attendance_records_status"),
        sa.UniqueConstraint("session_id", "submission_training_id", name="uq_training_attendance_session_participant"),
        sa.UniqueConstraint("token_hash", name="uq_training_attendance_records_token_hash"),
    )
    op.create_index("ix_training_attendance_records_session_id", "training_attendance_records", ["session_id"])
    op.create_index("ix_training_attendance_records_submission_training_id", "training_attendance_records", ["submission_training_id"])
    op.create_index("ix_training_attendance_records_token_hash", "training_attendance_records", ["token_hash"])

    op.create_table(
        "training_surveys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("form_id", sa.Integer(), sa.ForeignKey("forms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("training_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("anonymous", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('draft', 'active', 'closed')", name="ck_training_surveys_status"),
    )
    op.create_index("ix_training_surveys_training", "training_surveys", ["form_id", "training_id"])

    op.create_table(
        "training_survey_questions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("survey_id", sa.Integer(), sa.ForeignKey("training_surveys.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("question_type", sa.String(32), nullable=False),
        sa.Column("options_json", sa.JSON(), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("question_type IN ('scale', 'single_choice', 'multiple_choice', 'text')", name="ck_training_survey_questions_type"),
    )
    op.create_index("ix_training_survey_questions_order", "training_survey_questions", ["survey_id", "sort_order"])

    op.create_table(
        "training_survey_invitations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("survey_id", sa.Integer(), sa.ForeignKey("training_surveys.id", ondelete="CASCADE"), nullable=False),
        sa.Column("submission_training_id", sa.Integer(), sa.ForeignKey("submission_trainings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("survey_id", "submission_training_id", name="uq_training_survey_invitation_participant"),
        sa.UniqueConstraint("token_hash", name="uq_training_survey_invitations_token_hash"),
    )
    op.create_index("ix_training_survey_invitations_survey_id", "training_survey_invitations", ["survey_id"])
    op.create_index("ix_training_survey_invitations_submission_training_id", "training_survey_invitations", ["submission_training_id"])
    op.create_index("ix_training_survey_invitations_token_hash", "training_survey_invitations", ["token_hash"])

    op.create_table(
        "training_survey_responses",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("invitation_id", sa.Integer(), sa.ForeignKey("training_survey_invitations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("invitation_id", name="uq_training_survey_response_invitation"),
    )

    op.create_table(
        "training_survey_answers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("response_id", sa.Integer(), sa.ForeignKey("training_survey_responses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_id", sa.Integer(), sa.ForeignKey("training_survey_questions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("response_id", "question_id", name="uq_training_survey_answer_question"),
    )


def downgrade() -> None:
    op.drop_table("training_survey_answers")
    op.drop_table("training_survey_responses")
    op.drop_index("ix_training_survey_invitations_token_hash", table_name="training_survey_invitations")
    op.drop_index("ix_training_survey_invitations_submission_training_id", table_name="training_survey_invitations")
    op.drop_index("ix_training_survey_invitations_survey_id", table_name="training_survey_invitations")
    op.drop_table("training_survey_invitations")
    op.drop_index("ix_training_survey_questions_order", table_name="training_survey_questions")
    op.drop_table("training_survey_questions")
    op.drop_index("ix_training_surveys_training", table_name="training_surveys")
    op.drop_table("training_surveys")
    op.drop_index("ix_training_attendance_records_token_hash", table_name="training_attendance_records")
    op.drop_index("ix_training_attendance_records_submission_training_id", table_name="training_attendance_records")
    op.drop_index("ix_training_attendance_records_session_id", table_name="training_attendance_records")
    op.drop_table("training_attendance_records")
    op.drop_index("ix_training_attendance_sessions_training", table_name="training_attendance_sessions")
    op.drop_table("training_attendance_sessions")
    op.drop_index("ix_training_action_history_scope", table_name="training_participant_action_history")
    op.drop_index("ix_training_participant_history_training", table_name="training_participant_action_history")
    op.drop_table("training_participant_action_history")
