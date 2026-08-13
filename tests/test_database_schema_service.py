from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, g
from sqlalchemy import Column, Integer, MetaData, Table, create_engine
from sqlalchemy.exc import OperationalError

import manage
import routes.admin.mail_settings as mail_settings_routes
from models import Base
from services.database_schema_service import (
    MIGRATION_HINT,
    check_database_schema,
    database_migration_status,
    prepare_database_schema,
    redact_database_url,
    run_database_upgrade,
)
from services.mail_dispatch_service import MailDispatchService


def test_schema_validator_reports_missing_columns_and_accepts_current_models():
    incomplete_engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    for table_name in ("forms", "email_logs", "site_footers", "mail_footers"):
        Table(table_name, metadata, Column("id", Integer, primary_key=True))
    metadata.create_all(incomplete_engine)

    missing = check_database_schema(engine=incomplete_engine)

    assert "user_instruction_config" in missing["forms"]
    assert "event_type" in missing["email_logs"]
    assert "social_show_labels" in missing["site_footers"]
    assert "logo_position" in missing["mail_footers"]

    current_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(current_engine)
    assert check_database_schema(engine=current_engine) == {}


def test_database_url_is_logged_without_password():
    redacted = redact_database_url(
        "postgresql+psycopg://db_user:super-secret@db.example.test:5432/forms"
    )

    assert "super-secret" not in redacted
    assert "***" in redacted
    assert "db.example.test" in redacted


def test_migration_status_reports_unversioned_unknown_and_head(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'status.db'}")
    status = database_migration_status(engine=engine)
    assert status["state"] == "unversioned"
    assert status["head_count"] == 1
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO alembic_version(version_num) VALUES ('unknown_revision')")
    status = database_migration_status(engine=engine)
    assert status["state"] == "unknown"
    assert status["unknown"] == ["unknown_revision"]


def test_database_upgrade_uses_alembic_api_and_requested_database(tmp_path):
    captured = {}
    (tmp_path / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
    (tmp_path / "migrations").mkdir()

    def fake_upgrade(config, revision):
        captured["url"] = config.get_main_option("sqlalchemy.url")
        captured["script_location"] = config.get_main_option("script_location")
        captured["revision"] = revision

    run_database_upgrade(
        "sqlite:///upgrade.db",
        project_root=tmp_path,
        upgrade=fake_upgrade,
    )

    assert captured["url"] == "sqlite:///upgrade.db"
    assert captured["script_location"] == str(tmp_path / "migrations")
    assert captured["revision"] == "head"


def test_prepare_schema_only_runs_upgrade_when_enabled(monkeypatch, caplog):
    app = Flask(__name__)
    app.config.update(
        DATABASE_URL="postgresql://user:secret@db.test/forms",
        AUTO_CREATE_DB_SCHEMA=False,
        AUTO_DB_MIGRATE=False,
    )
    calls = []
    monkeypatch.setattr(
        "services.database_schema_service.run_database_upgrade",
        lambda url: calls.append(url),
    )
    monkeypatch.setattr(
        "services.database_schema_service.check_database_schema",
        lambda url: {"forms": ["user_instruction_config"]},
    )
    monkeypatch.setattr(
        "services.database_schema_service.database_migration_status",
        lambda url: {"state": "behind", "current": ["old"], "heads": ["head"]},
    )

    missing = prepare_database_schema(app)

    assert calls == []
    assert missing == {"forms": ["user_instruction_config"]}
    assert MIGRATION_HINT in caplog.text
    assert "secret" not in caplog.text

    app.config["AUTO_DB_MIGRATE"] = True
    monkeypatch.setattr(
        "services.database_schema_service.database_migration_status",
        lambda url: {"state": "head", "current": ["head"], "heads": ["head"]},
    )
    monkeypatch.setattr(
        "services.database_schema_service.check_database_schema",
        lambda url: {},
    )
    assert prepare_database_schema(app) == {}
    assert calls == ["postgresql://user:secret@db.test/forms"]


def test_auto_migrate_takes_precedence_over_legacy_auto_create(monkeypatch):
    app = Flask(__name__)
    app.config.update(
        DATABASE_URL="sqlite:///auto.db",
        AUTO_CREATE_DB_SCHEMA=True,
        AUTO_DB_MIGRATE=True,
    )
    calls = []
    monkeypatch.setattr(
        "services.database_schema_service.run_database_upgrade",
        lambda url: calls.append(url),
    )
    monkeypatch.setattr(
        "services.database_schema_service.check_database_schema",
        lambda url: {},
    )
    monkeypatch.setattr(
        "services.database_schema_service.database_migration_status",
        lambda url: {"state": "head", "current": ["head"], "heads": ["head"]},
    )

    assert prepare_database_schema(app) == {}
    assert calls == ["sqlite:///auto.db"]


def test_prepare_schema_fails_start_when_automatic_upgrade_fails(monkeypatch):
    app = Flask(__name__)
    app.config.update(
        DATABASE_URL="sqlite:///broken.db",
        AUTO_CREATE_DB_SCHEMA=False,
        AUTO_DB_MIGRATE=True,
    )

    def fail_upgrade(url):
        raise RuntimeError("migration failed")

    monkeypatch.setattr(
        "services.database_schema_service.run_database_upgrade",
        fail_upgrade,
    )
    with pytest.raises(RuntimeError, match="migration failed"):
        prepare_database_schema(app)


def test_manage_db_check_and_upgrade_exit_codes(monkeypatch, capsys):
    monkeypatch.setattr(
        manage,
        "database_migration_status",
        lambda url: {"state": "head", "current": ["head"], "heads": ["head"], "head_count": 1, "unknown": []},
    )
    monkeypatch.setattr(
        manage,
        "check_database_schema",
        lambda url: {"email_logs": ["event_type"]},
    )
    assert manage.db_check("sqlite:///missing.db") == 1
    assert "email_logs" in capsys.readouterr().out

    monkeypatch.setattr(manage, "check_database_schema", lambda url: {})
    assert manage.db_check("sqlite:///ready.db") == 0

    calls = []
    monkeypatch.setattr(manage, "run_database_upgrade", lambda url: calls.append(url))
    assert manage.db_upgrade("sqlite:///ready.db") == 0
    assert calls == ["sqlite:///ready.db"]


class FailingSession:
    def __init__(self):
        self.rollback_calls = 0
        self.added = []

    def add(self, record):
        self.added.append(record)

    def commit(self):
        raise OperationalError("INSERT", {}, Exception("missing column"))

    def rollback(self):
        self.rollback_calls += 1


def test_smtp_log_failure_rolls_back_and_does_not_change_smtp_result(caplog):
    app = Flask(__name__)
    app.config["SMTP_TIMEOUT"] = 10
    db = FailingSession()

    with app.test_request_context("/"):
        g.admin_user = SimpleNamespace(id=7)
        saved = mail_settings_routes._save_smtp_attempt(
            db,
            form=None,
            status="sent",
        )

    assert saved is False
    assert db.rollback_calls == 1
    assert "Nie udało się zapisać logu testu SMTP" in caplog.text


def test_mail_dispatch_log_commit_failure_always_rolls_back(caplog):
    app = Flask(__name__)
    db = FailingSession()

    with app.app_context():
        committed = MailDispatchService()._commit_email_log(db)

    assert committed is False
    assert db.rollback_calls == 1
    assert "Sprawdź migracje bazy danych" in caplog.text
