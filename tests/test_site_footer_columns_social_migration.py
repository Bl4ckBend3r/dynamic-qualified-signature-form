import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, Integer, MetaData, String, Table, Text, create_engine, inspect, select


MIGRATION_MODULE = "migrations.versions.20260721_0020_site_footer_columns_social"


def _operations(connection):
    return Operations(MigrationContext.configure(connection))


def test_site_footer_columns_social_migration_is_idempotent_and_backfills(monkeypatch):
    migration = importlib.import_module(MIGRATION_MODULE)
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    footers = Table("site_footers", metadata, Column("id", Integer, primary_key=True), Column("html_body", Text, nullable=False, server_default=""))
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(footers.insert().values(id=1, html_body="<p>Legacy</p>"))
        monkeypatch.setattr(migration, "op", _operations(connection))
        migration.upgrade()
        migration.upgrade()
        migrated = Table("site_footers", MetaData(), autoload_with=connection)
        columns = {item["name"]: item for item in inspect(connection).get_columns("site_footers")}
        row = connection.execute(select(migrated)).mappings().one()

    assert {"left_html", "right_html", "layout", "social_links", "social_position", "social_icon_style"} <= set(columns)
    assert row["html_body"] == "<p>Legacy</p>"
    assert row["layout"] == "two_columns"
    assert row["social_position"] == "left"
    assert row["social_icon_style"] == "gold"


def test_site_footer_columns_social_downgrade_is_idempotent(monkeypatch):
    migration = importlib.import_module(MIGRATION_MODULE)
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "site_footers", metadata,
        Column("id", Integer, primary_key=True),
        Column("html_body", Text, nullable=False),
        Column("left_html", Text),
        Column("layout", String(30), nullable=False, server_default="two_columns"),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", _operations(connection))
        migration.downgrade()
        migration.downgrade()
        columns = {item["name"] for item in inspect(connection).get_columns("site_footers")}

    assert columns == {"id", "html_body"}
