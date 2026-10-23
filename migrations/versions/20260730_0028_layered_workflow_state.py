"""split workflow state into stage, decision, documents and final outcome

Revision ID: 20260730_0028
Revises: 20260729_0027
Create Date: 2026-07-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260730_0028"
down_revision = "20260729_0027"
branch_labels = None
depends_on = None


def _json_type():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return postgresql.JSONB()
    return sa.JSON()


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    columns = _column_names("form_submissions")
    if "workflow_stage" not in columns:
        op.add_column(
            "form_submissions",
            sa.Column("workflow_stage", sa.String(length=100), nullable=True),
        )
    if "final_outcome" not in columns:
        op.add_column(
            "form_submissions",
            sa.Column("final_outcome", sa.String(length=100), nullable=True),
        )
    if "document_states" not in columns:
        op.add_column(
            "form_submissions",
            sa.Column("document_states", _json_type(), nullable=True),
        )
    if "legacy_process_status" not in columns:
        op.add_column(
            "form_submissions",
            sa.Column("legacy_process_status", sa.String(length=100), nullable=True),
        )

    # Preserve values that may already have been written by a partial rollout.
    columns = _column_names("form_submissions")
    if {"legacy_process_status", "process_status"} <= columns:
        op.execute(
            """
            UPDATE form_submissions
            SET legacy_process_status = process_status
            WHERE legacy_process_status IS NULL
              AND process_status IS NOT NULL
            """
        )
    if {"workflow_stage", "workflow_step"} <= columns:
        op.execute(
            """
            UPDATE form_submissions
            SET workflow_stage = workflow_step
            WHERE workflow_stage IS NULL
              AND workflow_step IS NOT NULL
            """
        )

    # These audit columns are part of the same workflow v2 rollout. Keep them
    # idempotent as production databases may have received them independently.
    event_columns = _column_names("submission_workflow_events")
    if "decision_code" not in event_columns:
        op.add_column(
            "submission_workflow_events",
            sa.Column("decision_code", sa.String(length=128), nullable=True),
        )
    if "user_message" not in event_columns:
        op.add_column(
            "submission_workflow_events",
            sa.Column("user_message", sa.Text(), nullable=True),
        )
    if "side_effects" not in event_columns:
        op.add_column(
            "submission_workflow_events",
            sa.Column("side_effects", _json_type(), nullable=True),
        )


def downgrade() -> None:
    event_columns = _column_names("submission_workflow_events")
    for column_name in ("side_effects", "user_message", "decision_code"):
        if column_name in event_columns:
            op.drop_column("submission_workflow_events", column_name)

    submission_columns = _column_names("form_submissions")
    for column_name in (
        "legacy_process_status",
        "document_states",
        "final_outcome",
        "workflow_stage",
    ):
        if column_name in submission_columns:
            op.drop_column("form_submissions", column_name)
