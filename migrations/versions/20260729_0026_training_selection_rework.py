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


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "forms" in tables:
        columns = _column_names("forms")
        if "training_selection_open" not in columns:
            op.add_column(
                "forms",
                sa.Column(
                    "training_selection_open",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.true(),
                ),
            )
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
            sa.Column("agreement_id", sa.String(255), nullable=False, server_default=""),
            sa.Column("agreement_file_id", sa.Integer(), sa.ForeignKey("submission_files.id", ondelete="SET NULL"), nullable=True),
            sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("unselected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("submission_id", "training_id", name="uq_submission_trainings_submission_training"),
        )
        op.create_index("ix_submission_trainings_submission_id", "submission_trainings", ["submission_id"])
        op.create_index("ix_submission_trainings_training_id", "submission_trainings", ["training_id"])
        op.create_index("ix_submission_trainings_status", "submission_trainings", ["status"])
    else:
        training_columns = _column_names("submission_trainings")
        additions = (
            sa.Column("agreement_id", sa.String(255), nullable=False, server_default=""),
            sa.Column("unselected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        )
        for column in additions:
            if column.name not in training_columns:
                op.add_column("submission_trainings", column)


def downgrade() -> None:
    if "training_selection_open" in _column_names("forms"):
        op.drop_column("forms", "training_selection_open")
