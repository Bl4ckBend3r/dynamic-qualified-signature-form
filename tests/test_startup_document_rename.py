from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from models import UploadedDocumentRename
from scripts import rename_uploaded_documents as rename_script
from scripts.deployment.startup_document_rename import run_startup_document_rename
from services.nextcloud_storage import NextcloudStorage


@pytest.fixture
def startup(monkeypatch):
    monkeypatch.delenv("AUTO_RENAME_UPLOADED_DOCUMENTS", raising=False)
    monkeypatch.setenv("FLASK_ENV", "production")
    app = SimpleNamespace(config={"DATABASE_URL": "postgresql://example/db"}, logger=Mock())
    storage = NextcloudStorage("https://cloud.test", "user", "secret")
    engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    sessions = SimpleNamespace(kw={"bind": engine})
    container = SimpleNamespace(storage=storage, submission_repository=SimpleNamespace(session_factory=sessions))
    readiness = Mock(return_value={"status": "ready"})
    inspector = Mock()
    inspector.has_table.return_value = True
    inspector.get_columns.return_value = [{"name": c.name} for c in UploadedDocumentRename.__table__.columns]
    monkeypatch.setattr("sqlalchemy.inspect", lambda engine: inspector)
    auto = Mock(return_value={"phase": "verify", "errors": [], "result": {"applied": 2}})
    monkeypatch.setattr(rename_script, "_automatic_migration", auto)
    return app, container, readiness, inspector, auto


def test_startup_calls_existing_auto_with_application_services(startup):
    app, container, readiness, _, auto = startup
    run_startup_document_rename(app, container, readiness)
    auto.assert_called_once()
    renamer = auto.call_args.args[0]
    assert renamer.storage is container.storage
    assert renamer.session_factory is container.submission_repository.session_factory
    assert any("completed" in call.args[0] for call in app.logger.info.call_args_list)


@pytest.mark.parametrize("reason", ["disabled", "testing", "local", "tls", "schema", "journal", "columns", "storage"])
def test_startup_skips_unsafe_or_disabled_migration(startup, monkeypatch, reason):
    app, container, readiness, inspector, auto = startup
    if reason == "disabled":
        monkeypatch.setenv("AUTO_RENAME_UPLOADED_DOCUMENTS", "false")
    elif reason == "testing":
        app.config["TESTING"] = True
    elif reason == "local":
        monkeypatch.setenv("FLASK_ENV", "development")
    elif reason == "tls":
        container.storage.verify_ssl = False
    elif reason == "schema":
        readiness.return_value = {"status": "not_ready"}
    elif reason == "journal":
        inspector.has_table.return_value = False
    elif reason == "columns":
        inspector.get_columns.return_value = []
    elif reason == "storage":
        container.storage = object()
    run_startup_document_rename(app, container, readiness)
    auto.assert_not_called()


def test_startup_can_opt_in_outside_production(startup, monkeypatch):
    app, container, readiness, _, auto = startup
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.setenv("AUTO_RENAME_UPLOADED_DOCUMENTS", "true")
    run_startup_document_rename(app, container, readiness)
    auto.assert_called_once()


def test_startup_lock_contention_is_not_failure(startup):
    app, container, readiness, _, auto = startup
    auto.side_effect = rename_script.RenameError("locked", "MIGRATION_LOCKED")
    run_startup_document_rename(app, container, readiness)
    app.logger.error.assert_not_called()
    assert any("MIGRATION_LOCKED" in call.args[0] for call in app.logger.info.call_args_list)


def test_startup_logs_report_error_without_crashing(startup):
    app, container, readiness, _, auto = startup
    auto.return_value = {"phase": "dry-run", "errors": [{"reason_code": "UNEXPECTED_ERROR"}]}
    run_startup_document_rename(app, container, readiness)
    app.logger.error.assert_called_once()


def test_startup_logs_exception_without_sensitive_message(startup):
    app, container, readiness, _, auto = startup
    auto.side_effect = RuntimeError("sensitive information")
    run_startup_document_rename(app, container, readiness)
    app.logger.error.assert_called_once()
    assert "sensitive information" not in str(app.logger.error.call_args)
