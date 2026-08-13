import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


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
    assert {"submission_assignment_history", "form_assignment_states"} <= set(inspector.get_table_names())
    submission_columns = {item["name"] for item in inspector.get_columns("form_submissions")}
    assert {"assigned_to_user_id", "assigned_at", "assigned_by_user_id", "priority", "due_at"} <= submission_columns
    permission_columns = {item["name"] for item in inspector.get_columns("form_permissions")}
    assert {"can_review", "can_assign_submissions"} <= permission_columns
    indexes = {item["name"] for item in inspector.get_indexes("form_submissions")}
    assert {"ix_form_submissions_assigned_to_user_id", "ix_form_submissions_priority", "ix_form_submissions_due_at"} <= indexes
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.downgrade()
    inspector = sa.inspect(engine)
    assert "submission_assignment_history" not in inspector.get_table_names()
    assert "assigned_to_user_id" not in {item["name"] for item in inspector.get_columns("form_submissions")}
    engine.dispose()
