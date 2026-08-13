"""add secure resumable public form drafts

Revision ID: 20260813_0035
Revises: 20260813_0034
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260813_0035"
down_revision = "20260813_0034"
branch_labels = None
depends_on = None

_REQUIRED_COLUMNS = {
    "id", "public_id", "form_id", "form_version_id", "email", "data_json",
    "status", "token_hash", "expires_at", "completed_at", "last_autosave_at",
    "submit_started_at", "submission_id", "submission_public_id",
    "metadata_json", "created_at", "updated_at",
}


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "form_drafts" in inspector.get_table_names():
        missing = sorted(_REQUIRED_COLUMNS - {item["name"] for item in inspector.get_columns("form_drafts")})
        if missing:
            raise RuntimeError(
                "Partial form_drafts schema before migration 20260813_0035; "
                f"missing columns: {', '.join(missing)}. Restore a backup or repair the schema before retrying."
            )
        return
    op.create_table(
        "form_drafts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("form_id", sa.Integer(), sa.ForeignKey("forms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("form_version_id", sa.Integer(), sa.ForeignKey("form_versions.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("data_json", _json_type(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_autosave_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submit_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submission_id", sa.Integer(), sa.ForeignKey("form_submissions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("submission_public_id", sa.String(64), nullable=True),
        sa.Column("metadata_json", _json_type(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('active', 'submitted', 'expired', 'abandoned')", name="ck_form_drafts_status"),
        sa.UniqueConstraint("public_id", name="uq_form_drafts_public_id"),
        sa.UniqueConstraint("token_hash", name="uq_form_drafts_token_hash"),
        sa.UniqueConstraint("submission_id", name="uq_form_drafts_submission_id"),
        sa.UniqueConstraint("submission_public_id", name="uq_form_drafts_submission_public_id"),
    )
    op.create_index("ix_form_drafts_public_id", "form_drafts", ["public_id"])
    op.create_index("ix_form_drafts_token_hash", "form_drafts", ["token_hash"])
    op.create_index("ix_form_drafts_email", "form_drafts", ["email"])
    op.create_index("ix_form_drafts_form_email_status", "form_drafts", ["form_id", "email", "status"])
    op.create_index("ix_form_drafts_status_expires", "form_drafts", ["status", "expires_at"])


def downgrade():
    if "form_drafts" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("form_drafts")
