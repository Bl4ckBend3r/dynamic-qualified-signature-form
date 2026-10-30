import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_submission_attachment_migration_adds_versioned_metadata(monkeypatch):
    migration = importlib.import_module("migrations.versions.20260813_0033_submission_attachments")
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE submission_files (id INTEGER PRIMARY KEY, submission_id INTEGER NOT NULL)"
        )
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        columns = {item["name"] for item in sa.inspect(connection).get_columns("submission_files")}
        indexes = {item["name"] for item in sa.inspect(connection).get_indexes("submission_files")}

    assert {
        "field_key", "attachment_version", "category", "uploaded_by_source",
        "workflow_step_at_upload", "antivirus_status", "rejection_reason",
    } <= columns
    assert "ix_submission_files_attachment_field" in indexes
