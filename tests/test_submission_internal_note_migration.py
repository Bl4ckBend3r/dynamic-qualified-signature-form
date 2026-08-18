from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Boolean, Column, Integer, MetaData, Table, create_engine, inspect


def test_internal_note_migration_schema_and_permission_backfill(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'notes-migration.db'}"
    engine = create_engine(database_url)
    metadata = MetaData()
    Table("forms", metadata, Column("id", Integer, primary_key=True))
    Table("users", metadata, Column("id", Integer, primary_key=True))
    Table("form_submissions", metadata, Column("id", Integer, primary_key=True))
    permissions = Table(
        "form_permissions", metadata, Column("id", Integer, primary_key=True),
        Column("can_review", Boolean, nullable=False), Column("can_manage", Boolean, nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(permissions.insert().values(id=1, can_review=True, can_manage=False))
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.stamp(config, "20260818_0038")
    command.upgrade(config, "head")
    inspector = inspect(engine)
    assert {"submission_internal_notes", "submission_internal_note_revisions", "submission_internal_note_mentions"} <= set(inspector.get_table_names())
    columns = {column["name"] for column in inspector.get_columns("form_permissions")}
    assert {"can_view_internal_notes", "can_add_internal_notes", "can_manage_internal_notes"} <= columns
    revision_columns = {column["name"] for column in inspector.get_columns("submission_internal_note_revisions")}
    assert {"previous_is_important", "new_is_important"} <= revision_columns
    migrated_permissions = Table("form_permissions", MetaData(), autoload_with=engine)
    with engine.connect() as connection:
        row = connection.execute(migrated_permissions.select()).mappings().one()
        assert row["can_view_internal_notes"] is True
        assert row["can_add_internal_notes"] is True
        assert row["can_manage_internal_notes"] is False
