from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table, create_engine, inspect, text
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.schema import CreateTable

from models import Base


TABLES = {
    "training_participant_action_history",
    "training_attendance_sessions",
    "training_attendance_records",
    "training_surveys",
    "training_survey_questions",
    "training_survey_invitations",
    "training_survey_responses",
    "training_survey_answers",
}

RESPONSE_COLUMNS_0047 = {
    "attempt_number", "status", "started_at", "completed_at", "score_points",
    "max_points", "score_percent", "test_snapshot_json",
}


def _database_at_0043(tmp_path, filename="training-management.db"):
    database_url = f"sqlite:///{tmp_path / filename}"
    engine = create_engine(database_url)
    metadata = MetaData()
    Table("forms", metadata, Column("id", Integer, primary_key=True), Column("slug", String(255), nullable=False, unique=True))
    Table("users", metadata, Column("id", Integer, primary_key=True))
    Table("form_versions", metadata, Column("id", Integer, primary_key=True), Column("form_id", Integer, ForeignKey("forms.id"), nullable=False))
    Table("form_submissions", metadata, Column("id", Integer, primary_key=True), Column("form_slug", String(255), nullable=False), Column("form_version_id", Integer, ForeignKey("form_versions.id"), nullable=True))
    Table("submission_trainings", metadata, Column("id", Integer, primary_key=True), Column("submission_id", Integer, ForeignKey("form_submissions.id"), nullable=False), Column("training_id", String(255), nullable=False))
    Table("repeatable_group_item_decisions", metadata, Column("id", Integer, primary_key=True))
    metadata.create_all(engine)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.stamp(config, "20260824_0043")
    return engine, config


def _assert_0047_response_schema(engine):
    inspector = inspect(engine)
    assert RESPONSE_COLUMNS_0047 <= {item["name"] for item in inspector.get_columns("training_survey_responses")}
    foreign_keys = inspector.get_foreign_keys("training_survey_responses")
    assert any(
        item["constrained_columns"] == ["invitation_id"]
        and item["referred_table"] == "training_survey_invitations"
        and item["referred_columns"] == ["id"]
        for item in foreign_keys
    )
    indexes = inspector.get_indexes("training_survey_responses")
    assert any(not item.get("unique") and item["column_names"] == ["invitation_id"] for item in indexes)
    uniques = inspector.get_unique_constraints("training_survey_responses")
    assert not any(item["column_names"] == ["invitation_id"] for item in uniques)
    assert any(item["column_names"] == ["invitation_id", "attempt_number"] for item in uniques)


def test_training_management_migration_upgrade_and_downgrade(tmp_path):
    engine, config = _database_at_0043(tmp_path)
    command.upgrade(config, "head")
    assert TABLES <= set(inspect(engine).get_table_names())
    _assert_0047_response_schema(engine)
    command.downgrade(config, "20260824_0043")
    assert not (TABLES & set(inspect(engine).get_table_names()))
    command.upgrade(config, "head")
    assert TABLES <= set(inspect(engine).get_table_names())


def test_0047_retries_after_non_transactional_partial_schema(tmp_path):
    engine, config = _database_at_0043(tmp_path, "partial-0047.db")
    command.upgrade(config, "20260826_0046")
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE training_surveys ADD COLUMN survey_type VARCHAR(16) NOT NULL DEFAULT 'survey'"))
        connection.execute(text("ALTER TABLE training_surveys ADD COLUMN attempt_policy VARCHAR(24) NOT NULL DEFAULT 'single_attempt'"))
        connection.execute(text("ALTER TABLE training_surveys ADD COLUMN post_requires_attendance BOOLEAN NOT NULL DEFAULT 0"))
        connection.execute(text("CREATE INDEX ix_training_surveys_training_type ON training_surveys (form_id, training_id, survey_type, status)"))
        connection.execute(text("ALTER TABLE training_survey_questions ADD COLUMN is_scored BOOLEAN NOT NULL DEFAULT 0"))
        connection.execute(text("ALTER TABLE training_survey_questions ADD COLUMN points NUMERIC(10, 2) NOT NULL DEFAULT 0"))
        connection.execute(text("ALTER TABLE training_survey_questions ADD COLUMN correct_answers_json JSON NOT NULL DEFAULT '[]'"))
        connection.execute(text("ALTER TABLE training_survey_questions ADD COLUMN comparison_key VARCHAR(128) NOT NULL DEFAULT ''"))
    command.upgrade(config, "head")
    assert {"survey_type", "attempt_policy", "post_requires_attendance"} <= {item["name"] for item in inspect(engine).get_columns("training_surveys")}
    _assert_0047_response_schema(engine)


def test_0047_downgrade_refuses_to_delete_multiple_attempts(tmp_path):
    engine, config = _database_at_0043(tmp_path, "multiple-attempts.db")
    command.upgrade(config, "head")
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO training_survey_responses (invitation_id, attempt_number, status, test_snapshot_json) VALUES (77, 1, 'completed', '{}')"))
        connection.execute(text("INSERT INTO training_survey_responses (invitation_id, attempt_number, status, test_snapshot_json) VALUES (77, 2, 'completed', '{}')"))
    with pytest.raises(RuntimeError, match="multiple responses"):
        command.downgrade(config, "20260826_0046")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM training_survey_responses WHERE invitation_id = 77")) == 2


def test_training_management_models_compile_for_mariadb_and_postgresql():
    for dialect in (mysql.dialect(), postgresql.dialect()):
        for table_name in TABLES:
            ddl = str(CreateTable(Base.metadata.tables[table_name]).compile(dialect=dialect)).upper()
            assert "CREATE TABLE" in ddl
