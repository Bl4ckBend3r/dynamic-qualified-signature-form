"""Archive submissions without deleting related records or Nextcloud files.

Revision ID: 20261103_0050
Revises: 20261102_0049
"""

from alembic import op
import sqlalchemy as sa


revision = "20261103_0050"
down_revision = "20261102_0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    import importlib

    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = {column["name"]: column for column in inspector.get_columns("form_submissions")}
    expected = {"deleted_at": sa.DateTime, "deleted_by_user_id": sa.Integer}
    for name, kind in expected.items():
        if name in columns and (not isinstance(columns[name]["type"], kind) or not columns[name]["nullable"]):
            raise RuntimeError(f"Incompatible existing soft-delete column: {name}")
    indexes = {index["name"]: index for index in inspector.get_indexes("form_submissions")}
    index_name = "ix_form_submissions_deleted_at"
    if index_name in indexes and (indexes[index_name]["column_names"] != ["deleted_at"] or indexes[index_name]["unique"]):
        raise RuntimeError("Incompatible existing soft-delete index")

    # Some installations recorded the former code-review 0049 (soft delete),
    # so Alembic will skip the target's checkbox repair. That repair is
    # idempotent: lists and ambiguous answers are left untouched.
    repair = importlib.import_module("migrations.versions.20261102_0049_repair_checkbox_answers")
    original_op = repair.op
    try:
        repair.op = op
        repair.upgrade()
    finally:
        repair.op = original_op

    with op.batch_alter_table("form_submissions") as batch:
        if "deleted_at" not in columns:
            batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        if "deleted_by_user_id" not in columns:
            batch.add_column(sa.Column("deleted_by_user_id", sa.Integer(), nullable=True))
        if index_name not in indexes:
            batch.create_index(index_name, ["deleted_at"])


def downgrade() -> None:
    archived = op.get_bind().execute(
        sa.text("SELECT 1 FROM form_submissions WHERE deleted_at IS NOT NULL LIMIT 1")
    ).first()
    if archived:
        raise RuntimeError("Cannot downgrade while archived submissions exist: this would erase archive markers")
    with op.batch_alter_table("form_submissions") as batch:
        batch.drop_index("ix_form_submissions_deleted_at")
        batch.drop_column("deleted_by_user_id")
        batch.drop_column("deleted_at")
