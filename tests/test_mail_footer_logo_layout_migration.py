import importlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, inspect, select


MIGRATION_MODULE = "migrations.versions.20260720_0018_mail_footer_logo_layout"


def _operations(connection):
    return Operations(MigrationContext.configure(connection))


@pytest.mark.parametrize(
    "existing_columns",
    [
        set(),
        {"logo_width"},
        {"logo_height", "logo_position"},
        {"logo_width", "logo_height", "logo_position"},
    ],
    ids=["missing", "width-only", "height-and-position", "already-present"],
)
def test_mail_footer_logo_layout_migration_is_idempotent(monkeypatch, existing_columns):
    migration = importlib.import_module(MIGRATION_MODULE)
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    extra_columns = [
        Column(name, String(30) if name == "logo_position" else Integer, nullable=True)
        for name in ("logo_width", "logo_height", "logo_position")
        if name in existing_columns
    ]
    footers = Table(
        "mail_footers",
        metadata,
        Column("id", Integer, primary_key=True),
        *extra_columns,
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        values = {"id": 1}
        if "logo_position" in existing_columns:
            values["logo_position"] = None
        connection.execute(footers.insert().values(**values))
        monkeypatch.setattr(migration, "op", _operations(connection))
        migration.upgrade()
        migration.upgrade()
        migrated = Table("mail_footers", MetaData(), autoload_with=connection)
        columns = {item["name"]: item for item in inspect(connection).get_columns("mail_footers")}
        row = connection.execute(select(migrated)).mappings().one()

    assert {"logo_width", "logo_height", "logo_position"} <= set(columns)
    assert row["logo_width"] is None
    assert row["logo_height"] is None
    assert row["logo_position"] == "top"
    assert columns["logo_position"]["type"].length == 30
    if "logo_position" not in existing_columns:
        assert columns["logo_position"]["nullable"] is False


def test_mail_footer_logo_layout_downgrade_checks_columns(monkeypatch):
    migration = importlib.import_module(MIGRATION_MODULE)
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "mail_footers",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("logo_width", Integer, nullable=True),
        Column("logo_position", String(30), nullable=False, server_default="top"),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", _operations(connection))
        migration.downgrade()
        migration.downgrade()
        columns = {item["name"] for item in inspect(connection).get_columns("mail_footers")}

    assert columns == {"id"}
