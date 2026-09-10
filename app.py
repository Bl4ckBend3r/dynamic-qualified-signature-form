from __future__ import annotations

import os
from pathlib import Path

import click
from dotenv import load_dotenv
from flask import Flask, current_app, has_request_context, jsonify, request, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config, normalize_app_base_path
from form_loader import validate_submission
from routes.api import bp as api_bp
from routes.documents import bp as documents_bp
from routes.public_forms import bp as public_forms_bp
from services.container import create_services
from services.database_schema_service import database_readiness_status, prepare_database_schema
from services.observability import configure_logging, register_observability
from signature_verifier import check_signature_trust_configuration

load_dotenv()

import logging

logger = logging.getLogger(__name__)


def create_app(config_object=None, storage_override=None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object or Config)
    _apply_runtime_env_overrides(app, enabled=config_object is None)
    configure_logging(app)
    _configure_reverse_proxy(app)
    _validate_config(app)
    _validate_strict_mode_config(app)
    check_signature_trust_configuration(app.logger)
    if os.getenv("TEMP_DIR"):
        app.config["TEMP_DIR"] = Path(os.getenv("TEMP_DIR", ""))

    Path(app.config["TEMP_DIR"]).mkdir(parents=True, exist_ok=True)

    prepare_database_schema(app)
    container = create_services(app, storage_override=storage_override)
    app.extensions["services"] = container
    _register_legacy_extension_aliases(app, container)
    register_template_filters(app)

    register_context_processors(app)
    register_blueprints(app)
    register_observability(app)
    register_operational_routes(app)
    register_cli_commands(app)

    logger.info(
        "storage_configuration_loaded base_url_configured=%s credentials_configured=%s",
        bool(app.config["NEXTCLOUD_BASE_URL"]),
        bool(app.config["NEXTCLOUD_USERNAME"] and app.config["NEXTCLOUD_APP_PASSWORD"]),
        extra={"event": "storage_configuration_loaded", "operation": "storage_configuration"},
    )
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
    if "AUTO_DB_MIGRATE" in os.environ:
        app.config["AUTO_DB_MIGRATE"] = os.getenv(
            "AUTO_DB_MIGRATE", "false"
        ).strip().lower() in {"1", "true", "yes", "tak", "on"}
    if any(name in os.environ for name in ("APP_BASE_PATH", "APPLICATION_ROOT", "SCRIPT_NAME")):
        app.config["APP_BASE_PATH"] = normalize_app_base_path(
            os.getenv("APP_BASE_PATH")
            or os.getenv("APPLICATION_ROOT")
            or os.getenv("SCRIPT_NAME")
            or ""
        )
        app.config["APPLICATION_ROOT"] = app.config["APP_BASE_PATH"] or "/"
    if "PROXY_FIX" in os.environ:
        app.config["PROXY_FIX"] = os.getenv("PROXY_FIX", "false").strip().lower() in {"1", "true", "yes", "tak", "on"}
    for name in (
        "STRICT_DOCUMENT_METADATA_READ",
        "STRICT_WORKFLOW_HISTORY_READ",
        "STRICT_DECISION_AUDIT_READ",
        "REQUIRE_STRICT_READINESS_CHECK",
        "ALLOW_UNSCANNED_UPLOADS",
    ):
        if name in os.environ:
            app.config[name] = os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "tak", "on"}


def _configure_reverse_proxy(app: Flask) -> None:
    trusted_hops = int(app.config.get("TRUSTED_PROXY_HOPS") or 0)
    if not trusted_hops and app.config.get("PROXY_FIX"):
        trusted_hops = 1
    if not trusted_hops:
        return
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=trusted_hops,
        x_proto=trusted_hops,
        x_host=trusted_hops,
        x_prefix=trusted_hops,
    )


def _validate_config(app: Flask) -> None:
    production_like = str(app.config.get("ENV") or os.getenv("FLASK_ENV", "")).strip().lower() == "production"
    secret_key = str(app.config.get("SECRET_KEY") or "").strip()
    if production_like and secret_key in {"", "change-me-in-production"}:
        raise RuntimeError("SECRET_KEY must be configured in production.")
    if app.config.get("AUTO_DB_MIGRATE"):
        raise RuntimeError(
            "AUTO_DB_MIGRATE is disabled. Run alembic upgrade head before starting the application."
        )
    if production_like and app.config.get("AUTO_CREATE_DB_SCHEMA"):
        raise RuntimeError("AUTO_CREATE_DB_SCHEMA cannot be enabled in production.")
    if production_like and app.config.get("ALLOW_UNSCANNED_UPLOADS"):
        raise RuntimeError("ALLOW_UNSCANNED_UPLOADS cannot be enabled in production.")
    if production_like and not app.config.get("SESSION_COOKIE_SECURE"):
        raise RuntimeError("SESSION_COOKIE_SECURE must be enabled in production.")
    if production_like and not app.config.get("SESSION_COOKIE_HTTPONLY"):
        raise RuntimeError("SESSION_COOKIE_HTTPONLY must be enabled in production.")
    if production_like and str(app.config.get("SESSION_COOKIE_SAMESITE") or "").lower() not in {"lax", "strict"}:
        raise RuntimeError("SESSION_COOKIE_SAMESITE must be Lax or Strict in production.")


def register_operational_routes(app: Flask) -> None:
    @app.get("/live")
    def live():
        return jsonify({"status": "alive"}), 200

    @app.get("/health")
    def health():
        return jsonify({"status": "alive"}), 200

    @app.get("/ready")
    def ready():
        readiness = database_readiness_status(current_app.config.get("DATABASE_URL"))
        status_code = 200 if readiness["status"] == "ready" else 503
        if status_code != 200:
            metrics = current_app.extensions.get("observability_metrics")
            if metrics is not None:
                metrics.readiness_failures.inc()
            current_app.logger.error(
                "readiness_failed database=%s schema=%s",
                readiness["database"],
                readiness["schema"],
                extra={"event": "readiness_failed", "operation": "readiness"},
            )
        return jsonify(readiness), status_code


def _validate_strict_mode_config(app: Flask) -> None:
    flags = {
        "STRICT_DOCUMENT_METADATA_READ": bool(app.config.get("STRICT_DOCUMENT_METADATA_READ")),
        "STRICT_WORKFLOW_HISTORY_READ": bool(app.config.get("STRICT_WORKFLOW_HISTORY_READ")),
        "STRICT_DECISION_AUDIT_READ": bool(app.config.get("STRICT_DECISION_AUDIT_READ")),
    }
    active_flags = [name for name, enabled in flags.items() if enabled]
    if not active_flags:
        return
    logger.warning(
        "strict_mode_enabled active_flags=%s require_readiness_check=%s",
        ",".join(active_flags),
        bool(app.config.get("REQUIRE_STRICT_READINESS_CHECK")),
    )
    if app.config.get("REQUIRE_STRICT_READINESS_CHECK"):
        logger.warning(
            "strict_readiness_blocker reason=external_readiness_required active_flags=%s",
            ",".join(active_flags),
        )


def _register_legacy_extension_aliases(app: Flask, container) -> None:
    app.extensions["storage"] = container.storage
    app.extensions["storage_repository"] = container.storage_repository
    app.extensions["submission_repository"] = container.submission_repository
    app.extensions["workflow_service"] = container.workflow_service
    app.extensions["document_service"] = container.document_service
    app.extensions["notification_service"] = container.notification_service
    app.extensions["mail_dispatch_service"] = container.mail_dispatch_service
    app.extensions["submission_service"] = container.submission_service
    app.extensions["audit_log_service"] = container.audit_log_service
    app.extensions["access_token_service"] = container.access_token_service
    app.extensions["form_config_service"] = container.form_config_service
    app.extensions["rules_service"] = container.rules_service


def register_blueprints(app: Flask) -> None:
    from routes.training_public import bp as training_public_bp

    app.register_blueprint(public_forms_bp)
    app.register_blueprint(documents_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(training_public_bp)
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
        policy_result = app.extensions["services"].password_policy_service.validate(password)
        if not policy_result.valid:
            raise click.ClickException(policy_result.errors[0])
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
    from services.form_option_service import option_label, option_value
    from services.admin_submission_service import format_business_datetime

    app.jinja_env.filters["trusted_html"] = sanitize_trusted_html
    app.jinja_env.filters["option_label"] = option_label
    app.jinja_env.filters["option_value"] = option_value
    app.jinja_env.filters["business_datetime"] = lambda value, fmt="%Y-%m-%d %H:%M": format_business_datetime(
        value, fmt, timezone_name=app.config.get("APP_TIMEZONE", "Europe/Warsaw")
    )


def inject_globals():
    configured_base_path = normalize_app_base_path(current_app.config.get("APP_BASE_PATH"))
    request_base_path = normalize_app_base_path(request.script_root) if has_request_context() else ""
    app_base_path = configured_base_path or request_base_path
    return {
        "app_name": current_app.config["APP_NAME"],
        "app_base_path": app_base_path,
        "APP_BASE_PATH": app_base_path,
        "footer_service_documents": footer_service_documents(),
        "footer_contact": footer_contact(),
        "site_footer": site_footer_context(),
    }


def site_footer_context() -> dict:
    from services.footer_logo_service import build_site_footer_view

    if not current_app.config.get("DATABASE_URL"):
        return build_site_footer_view(None)
    try:
        from database import create_session_factory
        from models import SiteFooter
        from sqlalchemy import select

        with create_session_factory(current_app.config["DATABASE_URL"])() as db:
            footer = db.execute(
                select(SiteFooter)
                .where(SiteFooter.is_active.is_(True))
                .order_by(SiteFooter.id)
            ).scalars().first()
            return build_site_footer_view(
                footer,
                logo_url_builder=lambda logo: url_for(
                    "public_forms.logo_asset",
                    logo_id=logo.id,
                    filename=Path(logo.filename).name,
                ),
            )
    except Exception:
        current_app.logger.exception("Nie udało się pobrać konfiguracji stopki strony.")
        return build_site_footer_view(None)


def footer_contact() -> dict:
    if not current_app.config.get("DATABASE_URL"):
        return {}
    try:
        from database import create_session_factory
        from models import ContactPage
        from services.contact_page_service import default_contact_page, ensure_contact_defaults, normalized_phones
        from sqlalchemy import select

        with create_session_factory(current_app.config["DATABASE_URL"])() as db:
            page = db.execute(select(ContactPage).order_by(ContactPage.id)).scalar_one_or_none()
            if not page:
                page = default_contact_page()
            else:
                ensure_contact_defaults(page)
            return {
                "address": page.address or page.contact_details or "",
                "email": page.email or "",
                "phones": normalized_phones(page.phones),
            }
    except Exception:
        current_app.logger.exception("Nie udało się pobrać danych kontaktowych do stopki.")
        return {}


def footer_service_documents() -> list[dict]:
    if not current_app.config.get("DATABASE_URL"):
        return []
    try:
        from database import create_session_factory
        from models import ServiceDocument
        from services.site_document_service import SERVICE_DOCUMENT_TYPES
        from sqlalchemy import select

        with create_session_factory(current_app.config["DATABASE_URL"])() as db:
            documents = db.execute(select(ServiceDocument)).scalars().all()
            by_type = {document.document_type: document for document in documents}
            return [
                {"type": type_id, "title": by_type[type_id].title or label}
                for type_id, label in SERVICE_DOCUMENT_TYPES.items()
                if type_id in by_type and (by_type[type_id].content_html or by_type[type_id].storage_path)
            ]
    except Exception:
        current_app.logger.exception("Nie udało się pobrać dokumentów serwisu do stopki.")
        return []


def get_services():
    return current_app.extensions["services"]


if __name__ == "__main__":
    create_app().run(debug=Config.DEBUG, host="127.0.0.1", port=5000)
