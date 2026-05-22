import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

import sitecustomize  # noqa: F401,E402
import nextcloud_assets_patch  # noqa: F401,E402
import documents_status_patch  # noqa: F401,E402
import form_notifications_patch  # noqa: F401,E402


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "tak", "on"}


def _env_list(name: str) -> list[str]:
    value = os.getenv(name, "")
    return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]


class Config:
    APP_NAME = "Formularze Lubuskie"
    DEBUG = os.getenv("FLASK_DEBUG", "true").lower() == "true"
    SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")

    BASE_DIR = Path(__file__).resolve().parent
    TEMPLATE_DIR = BASE_DIR / "templates"
    STATIC_DIR = BASE_DIR / "static"
    TEMP_DIR = Path(os.getenv("TEMP_DIR", str(BASE_DIR / "tmp")))

    NEXTCLOUD_BASE_URL = os.getenv("NEXTCLOUD_BASE_URL", "")
    NEXTCLOUD_USERNAME = os.getenv("NEXTCLOUD_USERNAME", "")
    NEXTCLOUD_APP_PASSWORD = os.getenv("NEXTCLOUD_APP_PASSWORD", "")
    NEXTCLOUD_FORMS_DIR = os.getenv("NEXTCLOUD_FORMS_DIR", "Formularze")
    NEXTCLOUD_OUTPUT_DIR = os.getenv("NEXTCLOUD_OUTPUT_DIR", "output")

    FORMS_DIR = BASE_DIR / "forms"
    OUTPUT_DIR = BASE_DIR / "output"
    PDF_OUTPUT_DIR = TEMP_DIR / "pdfs"
    CSV_OUTPUT_DIR = TEMP_DIR / "csv"
    SIGNED_OUTPUT_DIR = TEMP_DIR / "signed"
    SIGNATURE_WORK_DIR = TEMP_DIR / "signatures"

    FORM_DEFINITION_PATH = FORMS_DIR / os.getenv("FORM_JSON_FILE", "sample_form.json")
    CSV_FILENAME = os.getenv("CSV_FILENAME", "dane.csv")

    SIGNATURE_PROVIDER = os.getenv("SIGNATURE_PROVIDER", "mock")
    SIGNATURE_MOCK_MODE = os.getenv("SIGNATURE_MOCK_MODE", "signed").lower()
    SIGNATURE_API_BASE_URL = os.getenv("SIGNATURE_API_BASE_URL", "")
    SIGNATURE_API_TOKEN = os.getenv("SIGNATURE_API_TOKEN", "")

    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    MAIL_FROM = os.getenv("MAIL_FROM", SMTP_USER)
    SMTP_USE_TLS = _env_bool("SMTP_USE_TLS", "true")
    SMTP_USE_SSL = _env_bool("SMTP_USE_SSL", "false")
    SMTP_TIMEOUT = int(os.getenv("SMTP_TIMEOUT", "30"))

    FORM_NOTIFICATION_EMAILS = _env_list("FORM_NOTIFICATION_EMAILS")
