import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, inspect


MIGRATION_MODULE = "migrations.versions.20260720_0019_site_footer_configuration"


def _operations(connection):
    return Operations(MigrationContext.configure(connection))


def test_site_footer_migration_is_idempotent_and_downgrade_safe(monkeypatch):
    migration = importlib.import_module(MIGRATION_MODULE)
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table("logos", metadata, Column("id", Integer, primary_key=True), Column("name", String(255)))
    metadata.create_all(engine)

    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", _operations(connection))
        migration.upgrade()
        migration.upgrade()
        columns = {item["name"]: item for item in inspect(connection).get_columns("site_footers")}
        indexes = {item["name"] for item in inspect(connection).get_indexes("site_footers")}
        migration.downgrade()
        migration.downgrade()
        tables_after_downgrade = set(inspect(connection).get_table_names())

    assert {
        "id",
        "name",
        "html_body",
        "logo_path",
        "logo_id",
        "logo_alignment",
        "logo_width",
        "logo_height",
        "logo_position",
        "is_active",
        "created_at",
        "updated_at",
    } == set(columns)
    assert "ix_site_footers_logo_id" in indexes
    assert "site_footers" not in tables_after_downgrade
