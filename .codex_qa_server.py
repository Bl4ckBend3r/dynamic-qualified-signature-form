from pathlib import Path
from uuid import uuid4

from werkzeug.security import generate_password_hash

import app as app_module
from config import Config
from database import create_session_factory
from models import Form, FormSubmission, User
from services.admin_form_service import sync_form_fields
from tests.conftest import InMemoryStorage


definition = {
    "title": "QA warunków",
    "fields": [
        {"name": "consent", "label": "Zgoda", "type": "checkbox"},
        {
            "name": "region",
            "label": "Region",
            "type": "select",
            "options": ["lubuskie", "wielkopolskie"],
        },
        {
            "name": "channel",
            "label": "Kanał",
            "type": "radio",
            "options": ["online", "stacjonarnie"],
        },
        {
            "name": "topics",
            "label": "Tematy",
            "type": "multi_select",
            "options": ["Excel", "Kadry", "Python"],
        },
        {"name": "age", "label": "Wiek", "type": "number"},
        {"name": "start_date", "label": "Data rozpoczęcia", "type": "date"},
        {"name": "notes", "label": "Uwagi", "type": "text"},
    ],
}
qa_dir = Path(__file__).resolve().parent / "tmp" / f"codex-admin-qa-{uuid4().hex}"


class QaConfig(Config):
    TESTING = False
    WTF_CSRF_ENABLED = False
    SECRET_KEY = "qa-secret"
    DATABASE_URL = f"sqlite:///{qa_dir / 'qa.db'}"
    TEMP_DIR = qa_dir / "tmp"
    AUTO_CREATE_DB_SCHEMA = True
    NEXTCLOUD_BASE_URL = "https://nextcloud.invalid"
    NEXTCLOUD_USERNAME = "qa"
    NEXTCLOUD_APP_PASSWORD = "qa"
    NEXTCLOUD_FORMS_DIR = "Formularze"
    NEXTCLOUD_OUTPUT_DIR = "output"


QaConfig.TEMP_DIR.mkdir(parents=True, exist_ok=True)
application = app_module.create_app(
    config_object=QaConfig,
    storage_override=InMemoryStorage(definition),
)
session_factory = create_session_factory(QaConfig.DATABASE_URL)
with session_factory() as db:
    user = User(
        email="qa-admin@example.com",
        password_hash=generate_password_hash("secret"),
        role="super_admin",
        is_active=True,
        is_blocked=False,
    )
    form = Form(
        slug="qa_form",
        name="QA form",
        title="QA form",
        definition_json=definition,
        is_active=True,
        is_public=True,
    )
    db.add_all([user, form])
    db.flush()
    sync_form_fields(db, form, definition)
    submission = FormSubmission(
        submission_id="qa-public-uuid",
        form_slug="qa_form",
        form_name="QA form",
        imiona="Jan",
        nazwisko="Testowy",
        email="qa@example.com",
        process_status="FORM_SUBMITTED",
        workflow_step="submission",
    )
    db.add(submission)
    db.commit()
    form_id = form.id
    submission_pk = submission.id


if __name__ == "__main__":
    print(f"QA_READY form_id={form_id} submission_pk={submission_pk}", flush=True)
    application.run(host="127.0.0.1", port=5059, use_reloader=False)
