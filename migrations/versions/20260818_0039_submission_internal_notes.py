"""add auditable internal submission notes

Revision ID: 20260818_0039
Revises: 20260818_0038
Create Date: 2026-08-18
"""

from alembic import op
import sqlalchemy as sa


revision = "20260818_0039"
down_revision = "20260818_0038"
branch_labels = None
depends_on = None


def _tables(inspector) -> set[str]:
    return set(inspector.get_table_names())


def _columns(inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    required = {"form_submissions", "users", "forms", "form_permissions"}
    missing = sorted(required - _tables(inspector))
    if missing:
        raise RuntimeError(f"Missing prerequisite tables before 20260818_0039: {', '.join(missing)}")

    permission_columns = _columns(inspector, "form_permissions")
    with op.batch_alter_table("form_permissions") as batch:
        if "can_view_internal_notes" not in permission_columns:
            batch.add_column(sa.Column("can_view_internal_notes", sa.Boolean(), nullable=False, server_default=sa.false()))
        if "can_add_internal_notes" not in permission_columns:
            batch.add_column(sa.Column("can_add_internal_notes", sa.Boolean(), nullable=False, server_default=sa.false()))
        if "can_manage_internal_notes" not in permission_columns:
            batch.add_column(sa.Column("can_manage_internal_notes", sa.Boolean(), nullable=False, server_default=sa.false()))

    permission_columns = _columns(sa.inspect(bind), "form_permissions")
    if {"can_review", "can_manage"} <= permission_columns:
        permissions = sa.table(
            "form_permissions",
            sa.column("can_review", sa.Boolean()),
            sa.column("can_manage", sa.Boolean()),
            sa.column("can_view_internal_notes", sa.Boolean()),
            sa.column("can_add_internal_notes", sa.Boolean()),
            sa.column("can_manage_internal_notes", sa.Boolean()),
        )
        op.execute(
            permissions.update().values(
                can_view_internal_notes=permissions.c.can_review,
                can_add_internal_notes=permissions.c.can_review,
                can_manage_internal_notes=permissions.c.can_manage,
            )
        )

    tables = _tables(sa.inspect(bind))
    if "submission_internal_notes" not in tables:
        op.create_table(
            "submission_internal_notes",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("submission_id", sa.Integer(), sa.ForeignKey("form_submissions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("author_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("is_important", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("parent_note_id", sa.Integer(), sa.ForeignKey("submission_internal_notes.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("archived_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        )
        op.create_index("ix_submission_internal_notes_submission_created", "submission_internal_notes", ["submission_id", "created_at"])
        op.create_index("ix_submission_internal_notes_submission_important", "submission_internal_notes", ["submission_id", "is_important"])

    tables = _tables(sa.inspect(bind))
    if "submission_internal_note_revisions" not in tables:
        op.create_table(
            "submission_internal_note_revisions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("note_id", sa.Integer(), sa.ForeignKey("submission_internal_notes.id", ondelete="CASCADE"), nullable=False),
            sa.Column("previous_content", sa.Text(), nullable=False),
            sa.Column("new_content", sa.Text(), nullable=False),
            sa.Column("previous_is_important", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("new_is_important", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("edited_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("edited_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_submission_internal_note_revisions_note_edited", "submission_internal_note_revisions", ["note_id", "edited_at"])

    if "submission_internal_note_mentions" not in _tables(sa.inspect(bind)):
        op.create_table(
            "submission_internal_note_mentions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("note_id", sa.Integer(), sa.ForeignKey("submission_internal_notes.id", ondelete="CASCADE"), nullable=False),
            sa.Column("mentioned_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("note_id", "mentioned_user_id", name="uq_submission_internal_note_mention"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = _tables(sa.inspect(bind))
    for table in ("submission_internal_note_mentions", "submission_internal_note_revisions", "submission_internal_notes"):
        if table in tables:
            op.drop_table(table)
    if "form_permissions" in tables:
        columns = _columns(sa.inspect(bind), "form_permissions")
        with op.batch_alter_table("form_permissions") as batch:
            for column in ("can_manage_internal_notes", "can_add_internal_notes", "can_view_internal_notes"):
                if column in columns:
                    batch.drop_column(column)
