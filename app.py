from __future__ import annotations

import logging
import os
from pathlib import Path

import click
from dotenv import load_dotenv
from flask import Flask, current_app

from config import Config
from form_loader import validate_submission
from routes.api import bp as api_bp
from routes.documents import bp as documents_bp
from routes.public_forms import bp as public_forms_bp
from services.container import create_services, install_legacy_helpers

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


def create_app(config_object=None, storage_override=None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object or Config)
    _apply_runtime_env_overrides(app, enabled=config_object is None)
    _validate_config(app)
    if os.getenv("TEMP_DIR"):
        app.config["TEMP_DIR"] = Path(os.getenv("TEMP_DIR", ""))

    Path(app.config["TEMP_DIR"]).mkdir(parents=True, exist_ok=True)

    container = create_services(app, storage_override=storage_override)
    app.extensions["services"] = container
    _register_legacy_extension_aliases(app, container)
    install_legacy_helpers(app, container)
    register_template_filters(app)

    register_context_processors(app)
    register_blueprints(app)
    register_cli_commands(app)

    logger.info("NEXTCLOUD_BASE_URL=%s", app.config["NEXTCLOUD_BASE_URL"])
    logger.info("NEXTCLOUD_USERNAME=%s", app.config["NEXTCLOUD_USERNAME"])
    logger.info("NEXTCLOUD_FORMS_DIR=%s", app.config["NEXTCLOUD_FORMS_DIR"])
    logger.info("NEXTCLOUD_OUTPUT_DIR=%s", app.config["NEXTCLOUD_OUTPUT_DIR"])
    return app


def _apply_runtime_env_overrides(app: Flask, *, enabled: bool) -> None:
    if not enabled:
        return

    if "DATABASE_URL" in os.environ:
        app.config["DATABASE_URL"] = os.getenv("DATABASE_URL", "").strip()
    if "AUTO_CREATE_DB_SCHEMA" in os.environ:
        app.config["AUTO_CREATE_DB_SCHEMA"] = os.getenv(
            "AUTO_CREATE_DB_SCHEMA", "false"
        ).strip().lower() in {"1", "true", "yes", "tak", "on"}


def _validate_config(app: Flask) -> None:
    production_like = str(app.config.get("ENV") or os.getenv("FLASK_ENV", "")).strip().lower() == "production"
    secret_key = str(app.config.get("SECRET_KEY") or "").strip()
    if production_like and secret_key in {"", "change-me-in-production"}:
        raise RuntimeError("SECRET_KEY must be configured in production.")


def _register_legacy_extension_aliases(app: Flask, container) -> None:
    app.extensions["storage"] = container.storage
    app.extensions["storage_repository"] = container.storage_repository
    app.extensions["submission_repository"] = container.submission_repository
    app.extensions["workflow_service"] = container.workflow_service
    app.extensions["document_service"] = container.document_service
    app.extensions["notification_service"] = container.notification_service
    app.extensions["submission_service"] = container.submission_service
    app.extensions["audit_log_service"] = container.audit_log_service
    app.extensions["access_token_service"] = container.access_token_service
    app.extensions["form_config_service"] = container.form_config_service
    app.extensions["rules_service"] = container.rules_service


def register_blueprints(app: Flask) -> None:
    app.register_blueprint(public_forms_bp)
    app.register_blueprint(documents_bp)
    app.register_blueprint(api_bp)
    try:
        from routes.admin import bp as admin_bp
    except ModuleNotFoundError as exc:
        if app.config.get("DATABASE_URL") or exc.name != "sqlalchemy":
            raise
        logger.warning("Panel /admin nie zostal zarejestrowany: brak SQLAlchemy.")
    else:
        app.register_blueprint(admin_bp)


def register_cli_commands(app: Flask) -> None:
    @app.cli.command("create-admin")
    @click.option("--email", required=True)
    @click.option("--password", required=True)
    def create_admin(email: str, password: str) -> None:
        from database import create_session_factory
        from models import User
        from werkzeug.security import generate_password_hash

        database_url = app.config.get("DATABASE_URL")
        if not database_url:
            raise click.ClickException("DATABASE_URL is required.")
        session_factory = create_session_factory(database_url)
        normalized_email = email.strip().lower()
        with session_factory() as db:
            existing = db.query(User).filter(User.email == normalized_email).one_or_none()
            if existing:
                click.echo("Uzytkownik juz istnieje.")
                return
            user = User(
                email=normalized_email,
                password_hash=generate_password_hash(password),
                role="super_admin",
                is_active=True,
                is_blocked=False,
            )
            db.add(user)
            db.commit()
        click.echo("Utworzono uzytkownika super_admin.")


def register_context_processors(app: Flask) -> None:
    app.context_processor(inject_globals)


def register_template_filters(app: Flask) -> None:
    from services.html_safety import sanitize_trusted_html

    app.jinja_env.filters["trusted_html"] = sanitize_trusted_html


def inject_globals():
    return {"app_name": current_app.config["APP_NAME"]}


def get_services():
    return current_app.extensions["services"]


if __name__ == "__main__":
    create_app().run(debug=Config.DEBUG, host="127.0.0.1", port=5000)
