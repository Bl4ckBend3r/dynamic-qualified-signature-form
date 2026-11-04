from __future__ import annotations

import importlib
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, inspect
from sqlalchemy.dialects import mysql, postgresql


def test_sla_migration_has_linear_parent_and_portable_json():
    migration = importlib.import_module("migrations.versions.20260818_0040_workflow_sla_deadlines")
    assert migration.revision == "20260818_0040"
    assert migration.down_revision == "20260818_0039"
    assert "JSON" in migration._json_type().compile(dialect=mysql.dialect()).upper()
    assert "JSONB" in migration._json_type().compile(dialect=postgresql.dialect()).upper()


def test_sla_migration_creates_portable_schema_from_0039(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'sla-migration.db'}"
    engine = create_engine(database_url)
    metadata = MetaData()
    Table("form_versions", metadata, Column("id", Integer, primary_key=True))
    Table("form_submissions", metadata, Column("id", Integer, primary_key=True))
    Table("users", metadata, Column("id", Integer, primary_key=True))
    Table("email_logs", metadata, Column("id", Integer, primary_key=True))
    metadata.create_all(engine)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.stamp(config, "20260818_0039")
    command.upgrade(config, "20260818_0040")
    inspector = inspect(engine)
    assert {
        "business_calendars",
        "business_calendar_holidays",
        "workflow_step_sla_definitions",
        "submission_step_deadlines",
        "submission_deadline_notifications",
    } <= set(inspector.get_table_names())
    uniques = {item["name"] for item in inspector.get_unique_constraints("submission_deadline_notifications")}
    assert "uq_submission_deadline_notification_delivery" in uniques
