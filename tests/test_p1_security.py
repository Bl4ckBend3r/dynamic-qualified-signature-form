from __future__ import annotations

import pytest
from flask import Flask

from app import _validate_config
from services.password_policy_service import PasswordPolicyService


def test_live_health_and_ready_without_database_are_separated(client):
    live = client.get("/live")
    health = client.get("/health")
    ready = client.get("/ready")

    assert live.status_code == 200
    assert live.get_json() == {"status": "alive"}
    assert health.status_code == 200
    assert health.get_json() == {"status": "alive"}
    assert ready.status_code == 503
    assert ready.get_json() == {
        "status": "not_ready",
        "database": "not_configured",
        "schema": "unknown",
    }


def test_health_routes_have_one_canonical_endpoint_each(app):
    routes = {
        rule.rule: rule.endpoint
        for rule in app.url_map.iter_rules()
        if rule.rule in {"/live", "/ready", "/health"}
    }

    assert routes == {
        "/live": "live",
        "/ready": "ready",
        "/health": "health",
    }
    assert sum(rule.rule == "/health" for rule in app.url_map.iter_rules()) == 1


def test_live_and_legacy_health_do_not_call_database_or_nextcloud(app, monkeypatch):
    monkeypatch.setattr(
        "app.database_readiness_status",
        lambda _url: (_ for _ in ()).throw(AssertionError("readiness called")),
    )
    app.extensions["services"].storage.ensure_base_structure = lambda: (_ for _ in ()).throw(
        AssertionError("Nextcloud called")
    )

    client = app.test_client()
    assert client.get("/live").status_code == 200
    assert client.get("/health").status_code == 200


def test_ready_returns_503_when_database_is_unavailable(app, monkeypatch):
    monkeypatch.setattr(
        "app.database_readiness_status",
        lambda _url: {
            "status": "not_ready",
            "database": "unavailable",
            "schema": "unknown",
        },
    )

    response = app.test_client().get("/ready")

    assert response.status_code == 503
    assert response.get_json()["database"] == "unavailable"


def test_production_config_requires_secure_cookies_and_disables_unsafe_bypasses():
    base = {
        "ENV": "production",
        "SECRET_KEY": "production-secret",
        "SESSION_COOKIE_SECURE": True,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "AUTO_DB_MIGRATE": False,
        "AUTO_CREATE_DB_SCHEMA": False,
        "ALLOW_UNSCANNED_UPLOADS": False,
    }
    for key in (
        "SESSION_COOKIE_SECURE",
        "SESSION_COOKIE_HTTPONLY",
        "AUTO_CREATE_DB_SCHEMA",
        "ALLOW_UNSCANNED_UPLOADS",
    ):
        app = Flask(__name__)
        app.config.update(base)
        app.config[key] = not bool(base[key])
        try:
            _validate_config(app)
        except RuntimeError:
            pass
        else:
            raise AssertionError(f"unsafe production setting was accepted: {key}")


@pytest.mark.parametrize(
    "override,error_fragment",
    [
        ({"SESSION_COOKIE_SECURE": False}, "SESSION_COOKIE_SECURE"),
        ({"ALLOW_UNSCANNED_UPLOADS": True}, "ALLOW_UNSCANNED_UPLOADS"),
        ({"AUTO_CREATE_DB_SCHEMA": True}, "AUTO_CREATE_DB_SCHEMA"),
    ],
)
def test_each_unsafe_production_setting_has_one_validation_failure(override, error_fragment):
    app = Flask(__name__)
    app.config.update(
        ENV="production",
        SECRET_KEY="production-secret",
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        AUTO_DB_MIGRATE=False,
        AUTO_CREATE_DB_SCHEMA=False,
        ALLOW_UNSCANNED_UPLOADS=False,
    )
    app.config.update(override)

    with pytest.raises(RuntimeError) as exc_info:
        _validate_config(app)

    assert error_fragment in str(exc_info.value)


def test_valid_production_config_passes_validation():
    app = Flask(__name__)
    app.config.update(
        ENV="production",
        SECRET_KEY="production-secret",
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        AUTO_DB_MIGRATE=False,
        AUTO_CREATE_DB_SCHEMA=False,
        ALLOW_UNSCANNED_UPLOADS=False,
    )

    _validate_config(app)


def test_password_policy_supports_passphrases_and_blocks_weak_passwords():
    policy = PasswordPolicyService()

    assert not policy.validate("elevenchars").valid
    assert policy.validate("correct horse battery staple").valid
    assert policy.validate("bardzo dluga fraza bez cyfr ani symboli").valid
    popular = policy.validate("passwordpassword")
    assert not popular.valid
    assert "Hasło jest zbyt popularne." in popular.errors
