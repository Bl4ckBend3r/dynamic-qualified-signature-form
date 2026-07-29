"""add durable participant training selections

Revision ID: 20260729_0026
Revises: 20260729_0025
"""

from alembic import op
import sqlalchemy as sa

revision = "20260729_0026"
down_revision = "20260729_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "forms" in tables:
        columns = {item["name"] for item in inspector.get_columns("forms")}
        if "training_selection_open" not in columns:
            op.add_column("forms", sa.Column("training_selection_open", sa.Boolean(), nullable=False, server_default=sa.true()))
    if "submission_trainings" not in tables:
        op.create_table(
            "submission_trainings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("submission_id", sa.Integer(), sa.ForeignKey("form_submissions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("training_id", sa.String(255), nullable=False),
            sa.Column("training_name_snapshot", sa.String(512), nullable=False, server_default=""),
            sa.Column("training_price_snapshot", sa.String(64), nullable=False, server_default=""),
            sa.Column("status", sa.String(64), nullable=False, server_default="selected"),
            sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("locked_by_event", sa.String(128), nullable=False, server_default=""),
            sa.Column("agreement_file_id", sa.Integer(), sa.ForeignKey("submission_files.id", ondelete="SET NULL"), nullable=True),
            sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("submission_id", "training_id", name="uq_submission_trainings_submission_training"),
        )
        op.create_index("ix_submission_trainings_submission_id", "submission_trainings", ["submission_id"])
        op.create_index("ix_submission_trainings_training_id", "submission_trainings", ["training_id"])
        op.create_index("ix_submission_trainings_status", "submission_trainings", ["status"])


def downgrade() -> None:
    pass
