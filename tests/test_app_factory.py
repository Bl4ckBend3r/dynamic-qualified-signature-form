from pathlib import Path

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
