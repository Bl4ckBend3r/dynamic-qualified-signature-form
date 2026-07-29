import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import (
    Boolean,
    Column,
    Integer,
    JSON,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    inspect,
    select,
)
from sqlalchemy.orm import Session

from models import SubmissionTraining


SMTP_MIGRATION = "migrations.versions.20260727_0023_smtp_test_diagnostics"
SCHEMA_MIGRATION = (
    "migrations.versions.20260727_0024_ensure_extended_application_schema"
)
TRAINING_SNAPSHOT_MIGRATION = (
    "migrations.versions.20260729_0027_training_snapshot_details"
)


def _operations(connection):
    return Operations(MigrationContext.configure(connection))


def test_smtp_diagnostics_migration_accepts_manually_existing_columns(monkeypatch):
    migration = importlib.import_module(SMTP_MIGRATION)
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    logs = Table(
        "email_logs",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("event_type", String(100), nullable=True),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(logs.insert().values(id=1, event_type=None))
        monkeypatch.setattr(migration, "op", _operations(connection))
        migration.upgrade()
        migration.upgrade()

        columns = {column["name"] for column in inspect(connection).get_columns("email_logs")}
        row = connection.execute(
            select(Table("email_logs", MetaData(), autoload_with=connection))
        ).mappings().one()

    assert {"event_type", "error_type", "administrator_message"} <= columns
    assert row["event_type"] is None


def test_extended_schema_repair_is_idempotent_and_preserves_existing_data(monkeypatch):
    migration = importlib.import_module(SCHEMA_MIGRATION)
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    forms = Table(
        "forms",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("user_instruction", Text, nullable=True),
    )
    logs = Table("email_logs", metadata, Column("id", Integer, primary_key=True))
    site_footers = Table(
        "site_footers",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("html_body", Text, nullable=False),
        Column("layout", String(30), nullable=True),
        Column("social_links", JSON, nullable=True),
    )
    mail_footers = Table(
        "mail_footers",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("html_body", Text, nullable=False),
        Column("logo_path", String(1024), nullable=False),
        Column("logo_id", Integer, nullable=True),
        Column("logo_alignment", String(20), nullable=True),
        Column("links", JSON, nullable=True),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            forms.insert().values(id=1, user_instruction="Instrukcja legacy")
        )
        connection.execute(logs.insert().values(id=1))
        connection.execute(
            site_footers.insert().values(
                id=1,
                html_body="<p>Stara stopka strony</p>",
                layout=None,
                social_links=[{"url": "https://example.test"}],
            )
        )
        connection.execute(
            mail_footers.insert().values(
                id=1,
                html_body="<p>Stara stopka mailowa</p>",
                logo_path="legacy/logo.png",
                logo_id=17,
                logo_alignment=None,
                links=[{"label": "Kontakt"}],
            )
        )
        monkeypatch.setattr(migration, "op", _operations(connection))
        migration.upgrade()
        migration.upgrade()

        reflected = {
            name: Table(name, MetaData(), autoload_with=connection)
            for name in ("forms", "email_logs", "site_footers", "mail_footers")
        }
        columns = {
            name: {column["name"] for column in inspect(connection).get_columns(name)}
            for name in reflected
        }
        form_row = connection.execute(select(reflected["forms"])).mappings().one()
        log_row = connection.execute(select(reflected["email_logs"])).mappings().one()
        site_row = connection.execute(select(reflected["site_footers"])).mappings().one()
        mail_row = connection.execute(select(reflected["mail_footers"])).mappings().one()

    assert {"user_instruction", "user_instruction_config"} <= columns["forms"]
    assert {"event_type", "error_type", "administrator_message"} <= columns["email_logs"]
    assert {
        "left_html",
        "right_html",
        "layout",
        "social_links",
        "social_position",
        "social_icon_style",
        "social_show_labels",
    } <= columns["site_footers"]
    assert {
        "logo_alignment",
        "contact_html",
        "links",
        "legal_text",
        "use_global",
        "logo_width",
        "logo_height",
        "logo_position",
        "is_default",
        "is_active",
    } <= columns["mail_footers"]
    assert form_row["user_instruction"] == "Instrukcja legacy"
    assert form_row["user_instruction_config"] is None
    assert log_row["event_type"] is None
    assert site_row["html_body"] == "<p>Stara stopka strony</p>"
    assert site_row["layout"] == "two_columns"
    assert site_row["social_position"] == "left"
    assert site_row["social_icon_style"] == "gold"
    assert site_row["social_show_labels"] is False
    assert site_row["social_links"] == [{"url": "https://example.test"}]
    assert mail_row["html_body"] == "<p>Stara stopka mailowa</p>"
    assert mail_row["logo_path"] == "legacy/logo.png"
    assert mail_row["logo_id"] == 17
    assert mail_row["logo_alignment"] == "left"
    assert mail_row["links"] == [{"label": "Kontakt"}]
    assert mail_row["use_global"] is False
    assert mail_row["logo_position"] == "top"
    assert mail_row["is_default"] is False
    assert mail_row["is_active"] is True


def test_extended_schema_json_type_uses_jsonb_only_for_postgresql(monkeypatch):
    migration = importlib.import_module(SCHEMA_MIGRATION)

    class FakeBind:
        class Dialect:
            name = "postgresql"

        dialect = Dialect()

    monkeypatch.setattr(migration.op, "get_bind", lambda: FakeBind())
    assert migration._json_type().__class__.__name__ == "JSONB"

    FakeBind.Dialect.name = "mysql"
    assert migration._json_type().__class__.__name__ == "JSON"


def test_training_snapshot_migration_is_idempotent_and_preserves_rows(monkeypatch):
    migration = importlib.import_module(TRAINING_SNAPSHOT_MIGRATION)
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    trainings = Table(
        "submission_trainings",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("submission_id", Integer, nullable=True),
        Column("training_id", String(255), nullable=False),
        Column("training_name_snapshot", String(512), nullable=True),
        Column("training_price_snapshot", String(64), nullable=True),
        Column("status", String(64), nullable=True),
        Column("is_locked", Boolean, nullable=True),
        Column("locked_at", String(64), nullable=True),
        Column("locked_by_event", String(128), nullable=True),
        Column("agreement_id", String(255), nullable=True),
        Column("agreement_file_id", Integer, nullable=True),
        Column("selected_at", String(64), nullable=True),
        Column("unselected_at", String(64), nullable=True),
        Column("created_at", String(64), nullable=True),
        Column("updated_at", String(64), nullable=True),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            trainings.insert().values(id=1, training_id="stable-id")
        )
        monkeypatch.setattr(migration, "op", _operations(connection))
        migration.upgrade()
        migration.upgrade()

        reflected = Table(
            "submission_trainings",
            MetaData(),
            autoload_with=connection,
        )
        columns = {
            column["name"]
            for column in inspect(connection).get_columns(
                "submission_trainings"
            )
        }
        row = connection.execute(select(reflected)).mappings().one()
        with Session(bind=connection) as session:
            model_row = session.query(SubmissionTraining).one()
            assert model_row.training_id == "stable-id"
            assert model_row.training_snapshot is None
        migration.downgrade()
        migration.downgrade()
        columns_after_downgrade = {
            column["name"]
            for column in inspect(connection).get_columns(
                "submission_trainings"
            )
        }

    assert "training_snapshot" in columns
    assert row["training_id"] == "stable-id"
    assert row["training_snapshot"] is None
    assert "training_snapshot" not in columns_after_downgrade


def test_training_snapshot_json_type_uses_jsonb_only_for_postgresql(monkeypatch):
    migration = importlib.import_module(TRAINING_SNAPSHOT_MIGRATION)

    class FakeBind:
        class Dialect:
            name = "postgresql"

        dialect = Dialect()

    monkeypatch.setattr(migration.op, "get_bind", lambda: FakeBind())
    assert migration._json_type().__class__.__name__ == "JSONB"

    FakeBind.Dialect.name = "mysql"
    assert migration._json_type().__class__.__name__ == "JSON"
