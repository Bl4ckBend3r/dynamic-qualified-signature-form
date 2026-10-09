"""track per-training agreement lifecycle

Revision ID: 20260804_0030
Revises: 20260804_0029
"""

from alembic import op
import sqlalchemy as sa


revision = "20260804_0030"
down_revision = "20260804_0029"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    columns = _column_names("submission_trainings")
    for column in (
        sa.Column("agreement_generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("agreement_downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signed_agreement_uploaded_at", sa.DateTime(timezone=True), nullable=True),
    ):
        if column.name not in columns:
            op.add_column("submission_trainings", column)

    op.execute(sa.text("""
        UPDATE submission_trainings
        SET is_locked = :locked,
            locked_by_event = CASE WHEN locked_by_event = '' THEN 'historical_binding_agreement' ELSE locked_by_event END,
            locked_at = COALESCE(locked_at, updated_at),
            signed_agreement_uploaded_at = COALESCE(signed_agreement_uploaded_at, updated_at)
        WHERE status IN ('agreement_uploaded_by_beneficiary', 'agreement_waiting_for_office_signature', 'agreement_signed_by_office', 'locked')
           OR agreement_file_id IS NOT NULL
    """).bindparams(locked=True))


def downgrade() -> None:
    columns = _column_names("submission_trainings")
    for name in ("signed_agreement_uploaded_at", "agreement_downloaded_at", "agreement_generated_at"):
        if name in columns:
            op.drop_column("submission_trainings", name)
