import importlib
import os

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _assert_assignment_schema(inspector):
    tables = set(inspector.get_table_names())
    assert {"submission_assignment_history", "form_assignment_states"} <= tables

    submission_columns = {item["name"]: item for item in inspector.get_columns("form_submissions")}
    assert {"assigned_to_user_id", "assigned_at", "assigned_by_user_id", "priority", "due_at"} <= set(submission_columns)
    assert submission_columns["priority"]["nullable"] is False
    assert all(submission_columns[name]["nullable"] is True for name in (
        "assigned_to_user_id", "assigned_at", "assigned_by_user_id", "due_at"
    ))
    submission_indexes = {item["name"] for item in inspector.get_indexes("form_submissions")}
    assert {"ix_form_submissions_assigned_to_user_id", "ix_form_submissions_priority", "ix_form_submissions_due_at"} <= submission_indexes
    submission_checks = {item.get("name") for item in inspector.get_check_constraints("form_submissions")}
    assert "ck_form_submissions_priority" in submission_checks

    history_columns = {item["name"]: item for item in inspector.get_columns("submission_assignment_history")}
    assert {
        "id", "submission_id", "assigned_to_user_id", "assigned_by_user_id", "previous_user_id",
        "assigned_at", "unassigned_at", "reason", "source",
    } == set(history_columns)
    assert history_columns["assigned_at"]["nullable"] is False
    assert history_columns["reason"]["nullable"] is False
    assert history_columns["source"]["nullable"] is False
    assert all(history_columns[name]["nullable"] is True for name in (
        "submission_id", "assigned_to_user_id", "assigned_by_user_id", "previous_user_id", "unassigned_at"
    ))
    history_indexes = {item["name"] for item in inspector.get_indexes("submission_assignment_history")}
    assert {"ix_submission_assignment_history_submission_id", "ix_submission_assignment_history_assigned_at"} <= history_indexes
    history_checks = {item.get("name") for item in inspector.get_check_constraints("submission_assignment_history")}
    assert "ck_submission_assignment_history_source" in history_checks
    history_fks = {
        tuple(item["constrained_columns"]): (
            item["referred_table"], tuple(item["referred_columns"]), (item.get("options") or {}).get("ondelete")
        )
        for item in inspector.get_foreign_keys("submission_assignment_history")
    }
    assert history_fks[("submission_id",)] == ("form_submissions", ("id",), "SET NULL")
    for name in ("assigned_to_user_id", "assigned_by_user_id", "previous_user_id"):
        referred_table, referred_columns, ondelete = history_fks[(name,)]
        assert (referred_table, referred_columns) == ("users", ("id",))
        # MariaDB reflects explicit RESTRICT as the default NO ACTION/None.
        assert ondelete in {None, "RESTRICT", "NO ACTION"}

    state_columns = {item["name"]: item for item in inspector.get_columns("form_assignment_states")}
    assert {"form_id", "last_assigned_user_id", "updated_at"} == set(state_columns)
    assert state_columns["form_id"]["nullable"] is False
    assert state_columns["last_assigned_user_id"]["nullable"] is True
    assert state_columns["updated_at"]["nullable"] is False


def test_submission_assignment_migration_upgrade_and_downgrade(tmp_path, monkeypatch):
    database = tmp_path / "assignment-migration.db"
    engine = sa.create_engine(f"sqlite:///{database}")
    metadata = sa.MetaData()
    sa.Table("users", metadata, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table("forms", metadata, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table(
        "form_submissions", metadata, sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("submission_id", sa.String(64), nullable=False),
    )
    sa.Table(
        "form_permissions", metadata, sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("can_manage", sa.Boolean, nullable=False, server_default=sa.true()),
    )
    metadata.create_all(engine)
    migration = importlib.import_module("migrations.versions.20260813_0037_submission_assignments")
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()

    inspector = sa.inspect(engine)
    _assert_assignment_schema(inspector)
    permission_columns = {item["name"] for item in inspector.get_columns("form_permissions")}
    assert {"can_review", "can_assign_submissions"} <= permission_columns
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.downgrade()
    inspector = sa.inspect(engine)
    assert "submission_assignment_history" not in inspector.get_table_names()
    assert "assigned_to_user_id" not in {item["name"] for item in inspector.get_columns("form_submissions")}
    engine.dispose()


def test_upgraded_database_has_submission_assignment_schema():
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("DATABASE_URL is required for the migrated database schema check.")
    engine = sa.create_engine(database_url)
    try:
        _assert_assignment_schema(sa.inspect(engine))
    finally:
        engine.dispose()
