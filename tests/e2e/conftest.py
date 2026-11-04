from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from werkzeug.serving import make_server

from config import Config
from database import create_session_factory
from models import Form, FormSubmission, SubmissionWorkflowEvent
from services.admin_form_service import sync_form_fields
from services.form_version_service import FORM_VERSION_PUBLISHED
from services.submission_correction_service import SubmissionCorrectionService
from testing_support import InMemoryStorage


playwright_api = pytest.importorskip("playwright.sync_api")


E2E_FORM_DEFINITION = {
    "title": "Formularz E2E uczestnika",
    "description": "Izolowany formularz testowy.",
    "fields": [
        {"type": "text", "name": "imie", "label": "Imię", "required": True},
        {"type": "text", "name": "nazwisko", "label": "Nazwisko", "required": True},
        {"type": "email", "name": "email", "label": "Adres e-mail", "required": True},
        {
            "type": "text",
            "name": "readonly_note",
            "label": "Pole tylko do odczytu",
            "default": "Wartość systemowa",
            "readonly": True,
        },
    ],
    "documents": {
        "declaration": {"enabled": False},
        "agreement": {"enabled": False},
    },
}


@dataclass
class E2EEnvironment:
    app: object
    database_url: str
    form_definition: dict

    @property
    def session_factory(self):
        return create_session_factory(self.database_url)

    def submission_snapshot(self, public_id: str) -> dict:
        with self.session_factory() as db:
            submission = db.execute(
                select(FormSubmission).where(FormSubmission.submission_id == public_id)
            ).scalar_one()
            return {
                "id": submission.id,
                "submission_id": submission.submission_id,
                "access_token": submission.access_token,
                "process_status": submission.process_status,
                "workflow_step": submission.workflow_step,
                "data_json": dict(submission.data_json or {}),
            }

    def complete_submission(self, public_id: str) -> None:
        updated = self.app.extensions["services"].workflow_service.transition_to(
            public_id,
            "completed",
            actor="e2e-officer",
            metadata={"target_status": "COMPLETED", "reason": "e2e_happy_path"},
        )
        assert updated

    def return_for_correction(self, public_id: str) -> None:
        with self.session_factory() as db:
            submission = db.execute(
                select(FormSubmission).where(FormSubmission.submission_id == public_id)
            ).scalar_one()
            SubmissionCorrectionService().return_for_correction(
                db,
                submission,
                form_config=self.form_definition,
                reason="Korekta E2E",
                message_to_user="Popraw imię i wyślij formularz ponownie.",
                clear_submission=False,
                actor=SimpleNamespace(
                    id=1,
                    email="officer-e2e@example.invalid",
                    role="admin",
                ),
            )
            db.commit()

    def workflow_event_sources(self, public_id: str) -> list[str]:
        with self.session_factory() as db:
            return list(
                db.execute(
                    select(SubmissionWorkflowEvent.source).where(
                        SubmissionWorkflowEvent.public_submission_id == public_id
                    )
                ).scalars()
            )


@pytest.fixture()
def e2e_environment(tmp_path, monkeypatch):
    import app as app_module

    database_url = f"sqlite:///{tmp_path / 'participant-e2e.db'}"

    class E2ETestConfig(Config):
        ENV = "testing"
        TESTING = True
        SECRET_KEY = "participant-e2e-only-secret"
        DATABASE_URL = database_url
        AUTO_CREATE_DB_SCHEMA = True
        PUBLIC_CSRF_ENABLED = True
        NEXTCLOUD_BASE_URL = "https://nextcloud.invalid"
        NEXTCLOUD_USERNAME = "e2e"
        NEXTCLOUD_APP_PASSWORD = "e2e-only"
        NEXTCLOUD_FORMS_DIR = "Formularze"
        NEXTCLOUD_OUTPUT_DIR = "output"
        SMTP_HOST = "smtp.invalid"
        MAIL_FROM = "e2e@example.invalid"
        TEMP_DIR = tmp_path / "tmp"

    storage = InMemoryStorage(E2E_FORM_DEFINITION)

    def fake_generate_pdf(*, output_path, **_kwargs):
        Path(output_path).write_bytes(b"%PDF-1.4\n% participant e2e\n")

    monkeypatch.setattr("services.submission_service.generate_pdf", fake_generate_pdf)
    flask_app = app_module.create_app(
        config_object=E2ETestConfig,
        storage_override=storage,
    )
    flask_app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=True)
    Path(flask_app.config["TEMP_DIR"]).mkdir(parents=True, exist_ok=True)

    dispatched_mail: list[str] = []

    def fake_received_mail(public_id: str, *_args, **_kwargs):
        dispatched_mail.append(public_id)
        return SimpleNamespace(status="sent", error_message="")

    monkeypatch.setattr(
        flask_app.extensions["services"].mail_dispatch_service,
        "dispatch_submission_received",
        fake_received_mail,
    )

    session_factory = create_session_factory(database_url)
    with session_factory() as db:
        form = Form(
            slug="participant-e2e",
            name="Participant E2E",
            title=E2E_FORM_DEFINITION["title"],
            description=E2E_FORM_DEFINITION["description"],
            definition_json=E2E_FORM_DEFINITION,
            is_active=True,
            is_public=True,
            is_listed=True,
        )
        db.add(form)
        db.flush()
        sync_form_fields(db, form, E2E_FORM_DEFINITION)
        flask_app.extensions["services"].form_version_service.create_initial_version(
            db,
            form,
            actor_id=None,
            status=FORM_VERSION_PUBLISHED,
        )
        db.commit()

    environment = E2EEnvironment(
        app=flask_app,
        database_url=database_url,
        form_definition=E2E_FORM_DEFINITION,
    )
    environment.dispatched_mail = dispatched_mail
    return environment


@pytest.fixture()
def live_server(e2e_environment):
    server = make_server("127.0.0.1", 0, e2e_environment.app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.fixture()
def chromium_browser():
    with playwright_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except playwright_api.Error as exc:
            pytest.skip(f"Brak przeglądarki Chromium dla Playwright: {exc}")
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture()
def participant_page(chromium_browser):
    context = chromium_browser.new_context()
    page = context.new_page()
    try:
        yield page
    finally:
        context.close()


@pytest.fixture()
def submit_participant_form(live_server):
    def submit(page, *, first_name="Jan", last_name="Nowak", email="jan@example.invalid"):
        page.goto(f"{live_server}/form/participant-e2e")
        page.locator('[name="imie"]').fill(first_name)
        page.locator('[name="nazwisko"]').fill(last_name)
        page.locator('[name="email"]').fill(email)
        page.get_by_role("button", name="Wyślij", exact=True).click()
        page.get_by_test_id("submission-id").wait_for(state="visible")
        return page.get_by_test_id("submission-id").inner_text().strip()

    return submit
