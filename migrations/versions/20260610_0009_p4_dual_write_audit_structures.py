"""P4 dual-write audit structures

Revision ID: 20260610_0009
Revises: 20260605_0008
Create Date: 2026-06-10
"""

from alembic import op
import sqlalchemy as sa


revision = "20260610_0009"
down_revision = "20260605_0008"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _table_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _table_indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _table_columns(table_name):
        op.add_column(table_name, column)


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if index_name not in _table_indexes(table_name):
        op.create_index(index_name, table_name, columns)


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    if index_name in _table_indexes(table_name):
        op.drop_index(index_name, table_name=table_name)


def upgrade() -> None:
    tables = _table_names()
    if "submission_files" in tables:
        _add_column_if_missing("submission_files", sa.Column("original_filename", sa.String(512), nullable=False, server_default=""))
        _add_column_if_missing("submission_files", sa.Column("signature_status", sa.String(64), nullable=False, server_default=""))
        _add_column_if_missing("submission_files", sa.Column("signature_validation_result", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        _add_column_if_missing("submission_files", sa.Column("agreement_number", sa.String(255), nullable=False, server_default=""))
        _add_column_if_missing("submission_files", sa.Column("training_key", sa.String(255), nullable=False, server_default=""))
        _add_column_if_missing("submission_files", sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True))
        _add_column_if_missing("submission_files", sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True))
        _add_column_if_missing("submission_files", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
        _create_index_if_missing("ix_submission_files_document_type", "submission_files", ["document_type"])
        _create_index_if_missing("ix_submission_files_status", "submission_files", ["status"])
        _create_index_if_missing("ix_submission_files_created_at", "submission_files", ["created_at"])

    if "submission_workflow_events" not in tables:
        op.create_table(
            "submission_workflow_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("submission_id", sa.Integer(), sa.ForeignKey("form_submissions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("public_submission_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("form_slug", sa.String(255), nullable=False, server_default=""),
            sa.Column("previous_status", sa.String(128), nullable=False, server_default=""),
            sa.Column("new_status", sa.String(128), nullable=False, server_default=""),
            sa.Column("previous_step", sa.String(128), nullable=False, server_default=""),
            sa.Column("new_step", sa.String(128), nullable=False, server_default=""),
            sa.Column("actor_id", sa.Integer(), nullable=True),
            sa.Column("actor_email", sa.String(255), nullable=False, server_default=""),
            sa.Column("actor_role", sa.String(64), nullable=False, server_default="system"),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("source", sa.String(128), nullable=False, server_default="system"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_submission_workflow_events_submission_id", "submission_workflow_events", ["submission_id"])
        op.create_index("ix_submission_workflow_events_public_submission_id", "submission_workflow_events", ["public_submission_id"])
        op.create_index("ix_submission_workflow_events_created_at", "submission_workflow_events", ["created_at"])
        op.create_index("ix_submission_workflow_events_new_status", "submission_workflow_events", ["new_status"])

    if "submission_decisions" not in tables:
        op.create_table(
            "submission_decisions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("submission_id", sa.Integer(), sa.ForeignKey("form_submissions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("public_submission_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("form_slug", sa.String(255), nullable=False, server_default=""),
            sa.Column("decision", sa.String(64), nullable=False, server_default=""),
            sa.Column("justification", sa.Text(), nullable=False, server_default=""),
            sa.Column("officer_id", sa.Integer(), nullable=True),
            sa.Column("officer_email", sa.String(255), nullable=False, server_default=""),
            sa.Column("previous_status", sa.String(128), nullable=False, server_default=""),
            sa.Column("target_status", sa.String(128), nullable=False, server_default=""),
            sa.Column("email_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("email_sent", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("email_log_id", sa.Integer(), sa.ForeignKey("email_logs.id", ondelete="SET NULL"), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_submission_decisions_submission_id", "submission_decisions", ["submission_id"])
        op.create_index("ix_submission_decisions_public_submission_id", "submission_decisions", ["public_submission_id"])
        op.create_index("ix_submission_decisions_decision", "submission_decisions", ["decision"])
        op.create_index("ix_submission_decisions_created_at", "submission_decisions", ["created_at"])


def downgrade() -> None:
    tables = _table_names()
    if "submission_decisions" in tables:
        op.drop_table("submission_decisions")
    if "submission_workflow_events" in tables:
        op.drop_table("submission_workflow_events")
    if "submission_files" in tables:
        for index_name in (
            "ix_submission_files_created_at",
            "ix_submission_files_status",
            "ix_submission_files_document_type",
        ):
            _drop_index_if_exists(index_name, "submission_files")
        for column_name in (
            "updated_at",
            "signed_at",
            "generated_at",
            "training_key",
            "agreement_number",
            "signature_validation_result",
            "signature_status",
            "original_filename",
        ):
            if column_name in _table_columns("submission_files"):
                op.drop_column("submission_files", column_name)
