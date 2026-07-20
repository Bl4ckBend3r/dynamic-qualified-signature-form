import importlib
from datetime import datetime, timezone

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    inspect,
    select,
)
from sqlalchemy.orm import Session
from sqlalchemy.dialects import mysql, postgresql

from models import MailFooter


MIGRATION_MODULE = "migrations.versions.20260717_0017_mail_footer_configuration"
NEW_COLUMN_NAMES = {"logo_alignment", "contact_html", "links", "legal_text"}


def _operations(connection):
    return Operations(MigrationContext.configure(connection))


def _new_column(name):
    definitions = {
        "logo_alignment": Column("logo_alignment", String(20), nullable=True),
        "contact_html": Column("contact_html", Text, nullable=True),
        "links": Column("links", JSON, nullable=True),
        "legal_text": Column("legal_text", Text, nullable=True),
    }
    return definitions[name]


def test_mail_footer_links_uses_portable_json_type():
    links_type = MailFooter.__table__.c.links.type

    assert isinstance(links_type.dialect_impl(mysql.dialect()), mysql.JSON)
    assert isinstance(links_type.dialect_impl(postgresql.dialect()), postgresql.JSONB)


@pytest.mark.parametrize(
    "existing_columns",
    [
        (),
        ("logo_alignment",),
        ("logo_alignment", "contact_html"),
        ("logo_alignment", "contact_html", "links", "legal_text"),
    ],
    ids=["none", "logo-only", "partial", "all"],
)
def test_mail_footer_migration_completes_partially_migrated_tables(monkeypatch, existing_columns):
    migration = importlib.import_module(MIGRATION_MODULE)
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    footers = Table(
        "mail_footers",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("html_body", Text, nullable=True),
        *[_new_column(name) for name in existing_columns],
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            footers.insert().values(id=1, html_body="<p>Istniejąca stopka</p>")
        )
        monkeypatch.setattr(migration, "op", _operations(connection))
        migration.upgrade()
        migration.upgrade()

        migrated = Table("mail_footers", MetaData(), autoload_with=connection)
        column_details = {
            column["name"]: column
            for column in inspect(connection).get_columns("mail_footers")
        }
        row = connection.execute(select(migrated)).mappings().one()

    assert NEW_COLUMN_NAMES <= set(column_details)
    assert column_details["logo_alignment"]["type"].length == 20
    if "logo_alignment" not in existing_columns:
        assert column_details["logo_alignment"]["nullable"] is False
    assert column_details["contact_html"]["nullable"] is True
    assert column_details["links"]["nullable"] is True
    assert column_details["legal_text"]["nullable"] is True
    assert row["logo_alignment"] == "left"
    assert row["html_body"] == "<p>Istniejąca stopka</p>"


def test_mail_footer_migration_allows_mail_footer_orm_query(monkeypatch):
    migration = importlib.import_module(MIGRATION_MODULE)
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    legacy_footers = Table(
        "mail_footers",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("form_id", Integer, nullable=True),
        Column("name", String(255), nullable=False),
        Column("html_body", Text, nullable=False),
        Column("logo_path", String(1024), nullable=False),
        Column("logo_id", Integer, nullable=True),
        Column("is_default", Boolean, nullable=False),
        Column("is_active", Boolean, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    metadata.create_all(engine)

    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            legacy_footers.insert().values(
                id=1,
                form_id=None,
                name="Stopka ogólna",
                html_body="<p>Treść</p>",
                logo_path="",
                logo_id=None,
                is_default=True,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
        monkeypatch.setattr(migration, "op", _operations(connection))
        migration.upgrade()

        with Session(bind=connection) as session:
            footers = session.execute(select(MailFooter)).scalars().all()

    assert len(footers) == 1
    assert footers[0].name == "Stopka ogólna"
    assert footers[0].logo_alignment == "left"
    assert footers[0].contact_html is None
    assert footers[0].links is None
    assert footers[0].legal_text is None


def test_mail_footer_migration_downgrade_checks_columns_before_removing(monkeypatch):
    migration = importlib.import_module(MIGRATION_MODULE)
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "mail_footers",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("html_body", Text, nullable=True),
        Column("logo_alignment", String(20), nullable=False, server_default="left"),
        Column("legal_text", Text, nullable=True),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", _operations(connection))
        migration.downgrade()
        migration.downgrade()
        columns = {column["name"] for column in inspect(connection).get_columns("mail_footers")}

    assert columns == {"id", "html_body"}
