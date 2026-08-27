from __future__ import annotations

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


def test_password_policy_supports_passphrases_and_blocks_weak_passwords():
    policy = PasswordPolicyService()

    assert not policy.validate("elevenchars").valid
    assert policy.validate("correct horse battery staple").valid
    assert policy.validate("bardzo dluga fraza bez cyfr ani symboli").valid
    popular = policy.validate("passwordpassword")
    assert not popular.valid
    assert "Hasło jest zbyt popularne." in popular.errors
