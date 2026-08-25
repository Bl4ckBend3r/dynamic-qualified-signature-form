from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table, create_engine, inspect
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


def test_training_management_migration_upgrade_and_downgrade(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'training-management.db'}"
    engine = create_engine(database_url)
    metadata = MetaData()
    Table(
        "forms",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("slug", String(255), nullable=False, unique=True),
    )
    Table("users", metadata, Column("id", Integer, primary_key=True))
    Table(
        "form_versions",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("form_id", Integer, ForeignKey("forms.id"), nullable=False),
    )
    Table(
        "form_submissions",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("form_slug", String(255), nullable=False),
        Column("form_version_id", Integer, ForeignKey("form_versions.id"), nullable=True),
    )
    Table(
        "submission_trainings",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("submission_id", Integer, ForeignKey("form_submissions.id"), nullable=False),
        Column("training_id", String(255), nullable=False),
    )
    metadata.create_all(engine)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.stamp(config, "20260824_0043")
    command.upgrade(config, "head")
    assert TABLES <= set(inspect(engine).get_table_names())
    command.downgrade(config, "20260824_0043")
    assert not (TABLES & set(inspect(engine).get_table_names()))
    command.upgrade(config, "head")
    assert TABLES <= set(inspect(engine).get_table_names())


def test_training_management_models_compile_for_mariadb_and_postgresql():
    for dialect in (mysql.dialect(), postgresql.dialect()):
        for table_name in TABLES:
            ddl = str(CreateTable(Base.metadata.tables[table_name]).compile(dialect=dialect)).upper()
            assert "CREATE TABLE" in ddl
