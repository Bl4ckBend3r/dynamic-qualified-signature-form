from flask import render_template_string, url_for

from config import Config, normalize_app_base_path


def test_normalize_app_base_path_for_local_root():
    assert normalize_app_base_path("") == ""
    assert normalize_app_base_path("/") == ""


def test_normalize_app_base_path_for_prefixed_deployment():
    assert normalize_app_base_path("aplikacja") == "/aplikacja"
    assert normalize_app_base_path("/aplikacja/") == "/aplikacja"


def test_app_base_path_is_empty_locally(app):
    with app.test_request_context("/"):
        rendered = render_template_string("{{ app_base_path|tojson }}")

    assert rendered == '""'


def test_app_base_path_uses_configured_prefix(monkeypatch, tmp_path, form_definition):
    import app as app_module
    from conftest import InMemoryStorage

    class PrefixedConfig(Config):
        TESTING = True
        SERVER_NAME = "example.test"
        TEMP_DIR = tmp_path / "tmp"
        APP_BASE_PATH = "/aplikacja"
        APPLICATION_ROOT = "/aplikacja"
        DATABASE_URL = ""

    created = app_module.create_app(
        config_object=PrefixedConfig,
        storage_override=InMemoryStorage(form_definition),
    )

    with created.test_request_context("/do-podpisania"):
        rendered = render_template_string("{{ app_base_path|tojson }}")

    assert rendered == '"/aplikacja"'


def test_acceptance_status_url_for_respects_script_name(app):
    with app.test_request_context("/", environ_overrides={"SCRIPT_NAME": "/aplikacja"}):
        acceptance_url = url_for("api.api_acceptance_status", submission_id="abc")

    assert acceptance_url == "/aplikacja/api/submissions/abc/acceptance-status"


def test_base_template_renders_app_base_path_for_frontend(app):
    with app.test_request_context("/"):
        rendered = render_template_string(
            "{% extends 'base.html' %}{% block content %}ok{% endblock %}"
        )

    assert "window.APP_BASE_PATH = \"\";" in rendered


def test_base_template_renders_prefixed_app_base_path(monkeypatch, tmp_path, form_definition):
    import app as app_module
    from conftest import InMemoryStorage

    class PrefixedConfig(Config):
        TESTING = True
        SERVER_NAME = "example.test"
        TEMP_DIR = tmp_path / "tmp"
        APP_BASE_PATH = "/aplikacja"
        APPLICATION_ROOT = "/aplikacja"
        DATABASE_URL = ""

    created = app_module.create_app(
        config_object=PrefixedConfig,
        storage_override=InMemoryStorage(form_definition),
    )

    with created.test_request_context("/"):
        rendered = render_template_string(
            "{% extends 'base.html' %}{% block content %}ok{% endblock %}"
        )

    assert "window.APP_BASE_PATH = \"/aplikacja\";" in rendered
