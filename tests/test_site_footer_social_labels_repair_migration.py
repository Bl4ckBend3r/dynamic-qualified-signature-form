import importlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Boolean, Column, Integer, MetaData, Table, create_engine, inspect, select
from sqlalchemy.orm import Session

from models import Base, SiteFooter


MIGRATION_MODULE = "migrations.versions.20260721_0022_repair_site_footer_social_show_labels"


def _operations(connection):
    return Operations(MigrationContext.configure(connection))


@pytest.mark.parametrize("column_exists", [False, True], ids=["missing", "already-present"])
def test_repair_migration_is_idempotent_and_backfills_false(monkeypatch, column_exists):
    migration = importlib.import_module(MIGRATION_MODULE)
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    extra = [Column("social_show_labels", Boolean, nullable=True)] if column_exists else []
    footers = Table("site_footers", metadata, Column("id", Integer, primary_key=True), *extra)
    metadata.create_all(engine)

    with engine.begin() as connection:
        values = {"id": 1}
        if column_exists:
            values["social_show_labels"] = None
        connection.execute(footers.insert().values(**values))
        monkeypatch.setattr(migration, "op", _operations(connection))
        migration.upgrade()
        migration.upgrade()
        migrated = Table("site_footers", MetaData(), autoload_with=connection)
        row = connection.execute(select(migrated)).mappings().one()
        columns = {column["name"]: column for column in inspect(connection).get_columns("site_footers")}

    assert row["social_show_labels"] is False
    assert columns["social_show_labels"]["type"].python_type is bool
    if not column_exists:
        assert columns["social_show_labels"]["nullable"] is False


def test_site_footer_can_be_loaded_and_defaults_labels_to_false_after_repair(monkeypatch):
    migration = importlib.import_module(MIGRATION_MODULE)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(SiteFooter(name="Stopka", html_body="", is_active=True))
        session.commit()

    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE site_footers DROP COLUMN social_show_labels")
        monkeypatch.setattr(migration, "op", _operations(connection))
        migration.upgrade()

    with Session(engine) as session:
        footer = session.execute(select(SiteFooter)).scalar_one()
        assert footer.social_show_labels is False


def test_repair_migration_downgrade_only_drops_existing_column(monkeypatch):
    migration = importlib.import_module(MIGRATION_MODULE)
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table("site_footers", metadata, Column("id", Integer, primary_key=True))
    metadata.create_all(engine)

    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", _operations(connection))
        migration.downgrade()
        migration.upgrade()
        migration.downgrade()
        migration.downgrade()
        columns = {column["name"] for column in inspect(connection).get_columns("site_footers")}

    assert columns == {"id"}
