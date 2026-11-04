"""repair training participant action history schema

Revision ID: 20260824_0045
Revises: 20260824_0044
Create Date: 2026-08-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260824_0045"
down_revision = "20260824_0044"
branch_labels = None
depends_on = None


TABLE_NAME = "training_participant_action_history"
SUBMISSION_TRAINING_FK = "fk_training_action_history_submission_training"
FORM_FK = "fk_training_action_history_form"
SCOPE_INDEX = "ix_training_action_history_scope"


def _inspector(bind):
    return sa.inspect(bind)


def _column_map(bind) -> dict[str, dict]:
    return {
        column["name"]: column
        for column in _inspector(bind).get_columns(TABLE_NAME)
    }


def _foreign_key_matches(foreign_key: dict, column: str, target_table: str, ondelete: str) -> bool:
    options = foreign_key.get("options") or {}
    return (
        foreign_key.get("constrained_columns") == [column]
        and foreign_key.get("referred_table") == target_table
        and foreign_key.get("referred_columns") == ["id"]
        and str(options.get("ondelete") or "").upper() == ondelete
    )


def _drop_incorrect_foreign_keys(bind, column: str, target_table: str, ondelete: str) -> bool:
    matching_constraint_exists = False
    for foreign_key in _inspector(bind).get_foreign_keys(TABLE_NAME):
        if column not in (foreign_key.get("constrained_columns") or []):
            continue
        if _foreign_key_matches(foreign_key, column, target_table, ondelete):
            matching_constraint_exists = True
            continue
        constraint_name = foreign_key.get("name")
        if not constraint_name:
            raise RuntimeError(
                f"Nie można bezpiecznie zmienić nienazwanego FK kolumny {TABLE_NAME}.{column}."
            )
        with op.batch_alter_table(TABLE_NAME) as batch:
            batch.drop_constraint(constraint_name, type_="foreignkey")
    return matching_constraint_exists


def _create_foreign_key_if_missing(bind, name: str, column: str, target_table: str, ondelete: str) -> None:
    if any(
        _foreign_key_matches(item, column, target_table, ondelete)
        for item in _inspector(bind).get_foreign_keys(TABLE_NAME)
    ):
        return
    with op.batch_alter_table(TABLE_NAME) as batch:
        batch.create_foreign_key(name, target_table, [column], ["id"], ondelete=ondelete)


def _backfill_scope(bind) -> None:
    history = sa.table(
        TABLE_NAME,
        sa.column("id", sa.Integer()),
        sa.column("form_id", sa.Integer()),
        sa.column("training_id", sa.String(255)),
        sa.column("submission_training_id", sa.Integer()),
    )
    submission_trainings = sa.table(
        "submission_trainings",
        sa.column("id", sa.Integer()),
        sa.column("submission_id", sa.Integer()),
        sa.column("training_id", sa.String(255)),
    )
    submissions = sa.table(
        "form_submissions",
        sa.column("id", sa.Integer()),
        sa.column("form_slug", sa.String(255)),
        sa.column("form_version_id", sa.Integer()),
    )
    form_versions = sa.table(
        "form_versions",
        sa.column("id", sa.Integer()),
        sa.column("form_id", sa.Integer()),
    )
    forms = sa.table(
        "forms",
        sa.column("id", sa.Integer()),
        sa.column("slug", sa.String(255)),
    )

    version_form_id = (
        sa.select(form_versions.c.form_id)
        .select_from(
            submission_trainings
            .join(submissions, submissions.c.id == submission_trainings.c.submission_id)
            .join(form_versions, form_versions.c.id == submissions.c.form_version_id)
        )
        .where(submission_trainings.c.id == history.c.submission_training_id)
        .scalar_subquery()
    )
    slug_form_id = (
        sa.select(forms.c.id)
        .select_from(
            submission_trainings
            .join(submissions, submissions.c.id == submission_trainings.c.submission_id)
            .join(forms, forms.c.slug == submissions.c.form_slug)
        )
        .where(submission_trainings.c.id == history.c.submission_training_id)
        .scalar_subquery()
    )
    stable_training_id = (
        sa.select(submission_trainings.c.training_id)
        .where(submission_trainings.c.id == history.c.submission_training_id)
        .scalar_subquery()
    )

    bind.execute(
        sa.update(history)
        .where(history.c.form_id.is_(None))
        .values(form_id=sa.func.coalesce(version_form_id, slug_form_id))
    )
    bind.execute(
        sa.update(history)
        .where(history.c.training_id.is_(None))
        .values(training_id=stable_training_id)
    )


def _assert_scope_is_complete(bind) -> None:
    history = sa.table(
        TABLE_NAME,
        sa.column("form_id", sa.Integer()),
        sa.column("training_id", sa.String(255)),
    )
    missing = bind.scalar(
        sa.select(sa.func.count())
        .select_from(history)
        .where(sa.or_(history.c.form_id.is_(None), history.c.training_id.is_(None)))
    )
    if missing:
        raise RuntimeError(
            "Migracja 20260824_0045 nie może jednoznacznie uzupełnić form_id/training_id "
            f"dla {missing} rekordów training_participant_action_history. "
            "Rekordy pozostawiono bez usuwania; popraw powiązania submission_training_id i ponów migrację."
        )


def _ensure_scope_index(bind) -> None:
    indexes = {item["name"]: item for item in _inspector(bind).get_indexes(TABLE_NAME)}
    existing = indexes.get(SCOPE_INDEX)
    expected_columns = ["form_id", "training_id", "created_at"]
    if existing and existing.get("column_names") == expected_columns:
        return
    if existing:
        op.drop_index(SCOPE_INDEX, table_name=TABLE_NAME)
    op.create_index(SCOPE_INDEX, TABLE_NAME, expected_columns)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = _inspector(bind)
    required_tables = {
        TABLE_NAME,
        "submission_trainings",
        "form_submissions",
        "form_versions",
        "forms",
    }
    missing_tables = sorted(required_tables - set(inspector.get_table_names()))
    if missing_tables:
        raise RuntimeError(
            "Brak tabel wymaganych przez migrację 20260824_0045: " + ", ".join(missing_tables)
        )

    submission_columns = {
        item["name"] for item in inspector.get_columns("form_submissions")
    }
    required_submission_columns = {"id", "form_slug", "form_version_id"}
    if not required_submission_columns <= submission_columns:
        missing = sorted(required_submission_columns - submission_columns)
        raise RuntimeError(
            "Brak kolumn wymaganych do bezpiecznego backfillu form_id: " + ", ".join(missing)
        )

    columns = _column_map(bind)
    with op.batch_alter_table(TABLE_NAME) as batch:
        if "form_id" not in columns:
            batch.add_column(sa.Column("form_id", sa.Integer(), nullable=True))
        if "training_id" not in columns:
            batch.add_column(sa.Column("training_id", sa.String(255), nullable=True))

    _backfill_scope(bind)
    _assert_scope_is_complete(bind)

    _drop_incorrect_foreign_keys(bind, "form_id", "forms", "CASCADE")
    _drop_incorrect_foreign_keys(
        bind,
        "submission_training_id",
        "submission_trainings",
        "SET NULL",
    )

    columns = _column_map(bind)
    with op.batch_alter_table(TABLE_NAME) as batch:
        if columns["form_id"].get("nullable", True):
            batch.alter_column("form_id", existing_type=sa.Integer(), nullable=False)
        if columns["training_id"].get("nullable", True):
            batch.alter_column("training_id", existing_type=sa.String(255), nullable=False)
        if not columns["submission_training_id"].get("nullable", True):
            batch.alter_column(
                "submission_training_id",
                existing_type=sa.Integer(),
                nullable=True,
            )

    _create_foreign_key_if_missing(bind, FORM_FK, "form_id", "forms", "CASCADE")
    _create_foreign_key_if_missing(
        bind,
        SUBMISSION_TRAINING_FK,
        "submission_training_id",
        "submission_trainings",
        "SET NULL",
    )
    _ensure_scope_index(bind)


def downgrade() -> None:
    # This is a convergence migration: the canonical 0044 schema already has
    # the target columns, index and SET NULL relationship. Reintroducing the
    # divergent CASCADE/NOT NULL schema would either delete audit rows or fail
    # once legitimate history rows with submission_training_id=NULL exist.
    # Therefore downgrade preserves the canonical 0044 schema and all history.
    pass
