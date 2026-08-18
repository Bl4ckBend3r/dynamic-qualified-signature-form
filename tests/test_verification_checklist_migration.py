from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Boolean, Column, Integer, MetaData, String, Table, create_engine, inspect


def test_verification_checklist_migration_is_portable_schema(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    engine = create_engine(database_url)
    metadata = MetaData()
    Table("forms", metadata, Column("id", Integer, primary_key=True))
    Table("users", metadata, Column("id", Integer, primary_key=True))
    Table("form_versions", metadata, Column("id", Integer, primary_key=True), Column("form_id", Integer))
    Table("form_submissions", metadata, Column("id", Integer, primary_key=True))
    Table("submission_files", metadata, Column("id", Integer, primary_key=True))
    Table(
        "form_permissions", metadata, Column("id", Integer, primary_key=True),
        Column("can_manage", Boolean, nullable=False), Column("can_review", Boolean, nullable=False),
        Column("can_assign_submissions", Boolean, nullable=False),
    )
    metadata.create_all(engine)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.stamp(config, "20260813_0037")
    command.upgrade(config, "head")
    inspector = inspect(engine)
    assert {
        "verification_checklist_definitions",
        "verification_checklist_item_definitions",
        "submission_checklist_item_results",
        "submission_checklist_evidence",
        "submission_checklist_result_history",
    } <= set(inspector.get_table_names())
    permission_columns = {column["name"] for column in inspector.get_columns("form_permissions")}
    assert {"can_make_decision", "can_view_sensitive_data"} <= permission_columns
    result_columns = {column["name"] for column in inspector.get_columns("submission_checklist_item_results")}
    assert {"result", "comment", "officer_user_id", "reviewed_at"} <= result_columns
