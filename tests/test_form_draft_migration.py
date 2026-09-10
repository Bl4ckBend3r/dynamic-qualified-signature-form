import importlib

import sqlalchemy as sa
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_form_draft_migration_creates_secure_schema(monkeypatch):
    migration = importlib.import_module("migrations.versions.20260813_0035_public_form_drafts")
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table("forms", metadata, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table("form_versions", metadata, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table("form_submissions", metadata, sa.Column("id", sa.Integer, primary_key=True))
    metadata.create_all(engine)
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        columns = {item["name"] for item in sa.inspect(connection).get_columns("form_drafts")}
        indexes = {item["name"] for item in sa.inspect(connection).get_indexes("form_drafts")}
    assert {"public_id", "form_version_id", "token_hash", "expires_at", "last_autosave_at", "completed_at", "submission_id", "submission_public_id"} <= columns
    assert {"ix_form_drafts_token_hash", "ix_form_drafts_form_email_status"} <= indexes


def test_form_draft_migration_rejects_partial_existing_table(monkeypatch):
    migration = importlib.import_module("migrations.versions.20260813_0035_public_form_drafts")
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table("form_drafts", metadata, sa.Column("id", sa.Integer, primary_key=True))
    metadata.create_all(engine)
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        with pytest.raises(RuntimeError, match="Partial form_drafts schema"):
            migration.upgrade()
