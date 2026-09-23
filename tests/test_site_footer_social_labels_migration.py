import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, inspect, select


MIGRATION_MODULE = "migrations.versions.20260721_0021_site_footer_social_labels"


def _operations(connection):
    return Operations(MigrationContext.configure(connection))


def test_social_labels_migration_is_idempotent_and_defaults_to_false(monkeypatch):
    migration = importlib.import_module(MIGRATION_MODULE)
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    footers = Table("site_footers", metadata, Column("id", Integer, primary_key=True))
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(footers.insert().values(id=1))
        monkeypatch.setattr(migration, "op", _operations(connection))
        migration.upgrade()
        migration.upgrade()
        migrated = Table("site_footers", MetaData(), autoload_with=connection)
        row = connection.execute(select(migrated)).mappings().one()
        columns = {column["name"] for column in inspect(connection).get_columns("site_footers")}

    assert "social_show_labels" in columns
    assert row["social_show_labels"] is False


def test_social_labels_migration_downgrade_is_idempotent(monkeypatch):
    migration = importlib.import_module(MIGRATION_MODULE)
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table("site_footers", metadata, Column("id", Integer, primary_key=True))
    metadata.create_all(engine)

    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", _operations(connection))
        migration.upgrade()
        migration.downgrade()
        migration.downgrade()
        columns = {column["name"] for column in inspect(connection).get_columns("site_footers")}

    assert columns == {"id"}
