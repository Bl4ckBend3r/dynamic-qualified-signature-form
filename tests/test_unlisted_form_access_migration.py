import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_unlisted_bridge_repairs_either_historic_0033_shape(monkeypatch):
    migration = importlib.import_module("migrations.versions.20260813_0034_unlisted_form_access")
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE forms (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE submission_files (id INTEGER PRIMARY KEY, submission_id INTEGER NOT NULL)")
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        inspector = sa.inspect(connection)
        form_columns = {item["name"] for item in inspector.get_columns("forms")}
        file_columns = {item["name"] for item in inspector.get_columns("submission_files")}
        indexes = {item["name"] for item in inspector.get_indexes("submission_files")}
    assert {"is_listed", "share_token_hash"} <= form_columns
    assert {"field_key", "attachment_version", "antivirus_status", "rejection_reason"} <= file_columns
    assert "ix_submission_files_attachment_field" in indexes
