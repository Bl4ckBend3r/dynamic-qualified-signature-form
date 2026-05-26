from __future__ import annotations

import logging
import os
from pathlib import Path

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
    if os.getenv("TEMP_DIR"):
        app.config["TEMP_DIR"] = Path(os.getenv("TEMP_DIR", ""))

    Path(app.config["TEMP_DIR"]).mkdir(parents=True, exist_ok=True)

    container = create_services(app, storage_override=storage_override)
    app.extensions["services"] = container
    _register_legacy_extension_aliases(app, container)
    install_legacy_helpers(app, container)

    register_context_processors(app)
    register_blueprints(app)

    logger.info("NEXTCLOUD_BASE_URL=%s", app.config["NEXTCLOUD_BASE_URL"])
    logger.info("NEXTCLOUD_USERNAME=%s", app.config["NEXTCLOUD_USERNAME"])
    logger.info("NEXTCLOUD_FORMS_DIR=%s", app.config["NEXTCLOUD_FORMS_DIR"])
    logger.info("NEXTCLOUD_OUTPUT_DIR=%s", app.config["NEXTCLOUD_OUTPUT_DIR"])
    return app


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


def register_context_processors(app: Flask) -> None:
    app.context_processor(inject_globals)


def inject_globals():
    return {"app_name": current_app.config["APP_NAME"]}


def get_services():
    return current_app.extensions["services"]


if __name__ == "__main__":
    create_app().run(debug=Config.DEBUG, host="127.0.0.1", port=5000)
