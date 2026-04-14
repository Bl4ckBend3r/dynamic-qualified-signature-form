import os
from pathlib import Path


class Config:
    APP_NAME = "Formularze Lubuskie"
    DEBUG = os.getenv("FLASK_DEBUG", "true").lower() == "true"
    SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")

    BASE_DIR = Path(__file__).resolve().parent
    TEMPLATE_DIR = BASE_DIR / "templates"
    STATIC_DIR = BASE_DIR / "static"
    FORMS_DIR = BASE_DIR / "forms"
    OUTPUT_DIR = BASE_DIR / "output"

    FORM_DEFINITION_PATH = FORMS_DIR / os.getenv("FORM_JSON_FILE", "sample_form.json")

    PDF_OUTPUT_DIR = OUTPUT_DIR / "pdfs"
    CSV_OUTPUT_DIR = OUTPUT_DIR / "csv"
    SIGNED_OUTPUT_DIR = OUTPUT_DIR / "signed"
    SIGNATURE_WORK_DIR = OUTPUT_DIR / "signatures"

    CSV_FILENAME = os.getenv("CSV_FILENAME", "submissions.csv")

    SIGNATURE_PROVIDER = os.getenv("SIGNATURE_PROVIDER", "mock")
    SIGNATURE_MOCK_MODE = os.getenv("SIGNATURE_MOCK_MODE", "signed").lower()
    SIGNATURE_API_BASE_URL = os.getenv("SIGNATURE_API_BASE_URL", "")
    SIGNATURE_API_TOKEN = os.getenv("SIGNATURE_API_TOKEN", "")