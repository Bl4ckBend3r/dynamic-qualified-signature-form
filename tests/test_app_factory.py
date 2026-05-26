from pathlib import Path

from flask import Flask


def test_app_py_does_not_replace_module_with_sys_modules():
    source = Path("app.py").read_text(encoding="utf-8")

    assert "sys.modules" not in source


def test_create_app_returns_flask_app(app):
    assert isinstance(app, Flask)
    assert "services" in app.extensions


def test_blueprints_are_registered(app):
    assert {"public_forms", "documents", "api"}.issubset(set(app.blueprints))
