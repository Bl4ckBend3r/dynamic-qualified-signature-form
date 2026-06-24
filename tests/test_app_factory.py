from pathlib import Path
import logging

import pytest
from flask import Flask


def test_app_py_does_not_replace_module_with_sys_modules():
    source = Path("app.py").read_text(encoding="utf-8")

    assert "sys.modules" not in source


def test_create_app_returns_flask_app(app):
    assert isinstance(app, Flask)
    assert "services" in app.extensions


def test_blueprints_are_registered(app):
    assert {"public_forms", "documents", "api"}.issubset(set(app.blueprints))


def test_create_app_rejects_default_secret_key_in_production(monkeypatch, form_definition):
    import app as app_module
    from conftest import InMemoryStorage
    from config import Config

    class ProductionConfig(Config):
        ENV = "production"
        TESTING = True

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        app_module.create_app(config_object=ProductionConfig, storage_override=InMemoryStorage(form_definition))


def test_strict_flags_are_disabled_by_default():
    from config import Config

    assert Config.STRICT_DOCUMENT_METADATA_READ is False
    assert Config.STRICT_WORKFLOW_HISTORY_READ is False
    assert Config.STRICT_DECISION_AUDIT_READ is False
    assert Config.REQUIRE_STRICT_READINESS_CHECK is False


def test_create_app_logs_active_strict_flags_independently(monkeypatch, tmp_path, form_definition, caplog):
    import app as app_module
    from conftest import InMemoryStorage

    monkeypatch.setenv("FLASK_ENV", "testing")
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("TEMP_DIR", str(tmp_path / "strict-test"))
    monkeypatch.setenv("NEXTCLOUD_BASE_URL", "https://nextcloud.test")
    monkeypatch.setenv("NEXTCLOUD_USERNAME", "tester")
    monkeypatch.setenv("NEXTCLOUD_APP_PASSWORD", "secret")
    monkeypatch.setenv("STRICT_WORKFLOW_HISTORY_READ", "true")
    monkeypatch.delenv("STRICT_DOCUMENT_METADATA_READ", raising=False)
    monkeypatch.delenv("STRICT_DECISION_AUDIT_READ", raising=False)

    with caplog.at_level(logging.WARNING):
        created = app_module.create_app(storage_override=InMemoryStorage(form_definition))

    assert created.config["STRICT_WORKFLOW_HISTORY_READ"] is True
    assert created.config["STRICT_DOCUMENT_METADATA_READ"] is False
    assert "strict_mode_enabled" in caplog.text
    assert "STRICT_WORKFLOW_HISTORY_READ" in caplog.text
    assert "STRICT_DOCUMENT_METADATA_READ" not in caplog.text


def test_require_strict_readiness_check_logs_external_gate(monkeypatch, tmp_path, form_definition, caplog):
    import app as app_module
    from conftest import InMemoryStorage

    monkeypatch.setenv("FLASK_ENV", "testing")
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("TEMP_DIR", str(tmp_path / "strict-readiness-test"))
    monkeypatch.setenv("NEXTCLOUD_BASE_URL", "https://nextcloud.test")
    monkeypatch.setenv("NEXTCLOUD_USERNAME", "tester")
    monkeypatch.setenv("NEXTCLOUD_APP_PASSWORD", "secret")
    monkeypatch.setenv("STRICT_DECISION_AUDIT_READ", "true")
    monkeypatch.setenv("REQUIRE_STRICT_READINESS_CHECK", "true")

    with caplog.at_level(logging.WARNING):
        app_module.create_app(storage_override=InMemoryStorage(form_definition))

    assert "strict_mode_enabled" in caplog.text
    assert "strict_readiness_blocker" in caplog.text
    assert "external_readiness_required" in caplog.text
