from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from types import SimpleNamespace

from flask import Blueprint, Flask
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from models import Base, EmailLog, Form, FormDraft, FormField, FormSubmission, MailTemplate
from routes import public_forms
from services.form_draft_service import FormDraftService
from services.form_version_service import FORM_VERSION_PUBLISHED, FormVersionService
from services.mail_dispatch_service import MailDispatchService


def definition(label="Email v1"):
    return {
        "title": "Draft form",
        "fields": [{"name": "email", "label": label, "type": "email", "required": True}, {"name": "note", "label": "Note", "type": "text", "required": True}],
        "workflow": {"initial_step": "submission", "steps": [{"id": "submission"}]},
    }


def database(tmp_path):
    url = f"sqlite:///{tmp_path / 'drafts.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session.begin() as db:
        form = Form(slug="draft-form", name="Draft", title="Draft form", definition_json=definition(), is_active=True, is_public=True)
        db.add(form)
        db.flush()
        for order, (name, field_type, required) in enumerate((("email", "email", True), ("note", "text", True)), 1):
            db.add(FormField(form_id=form.id, name=name, label=name, type=field_type, required=required, options=[], default_value="", section="", stage="initial_submission", sort_order=order, active=True))
        version = FormVersionService().create_initial_version(db, form, actor_id=None, status=FORM_VERSION_PUBLISHED)
        version.definition_json = definition()
    return Session


def draft_app(monkeypatch, Session):
    app = Flask(__name__, template_folder=str(public_forms.Path(__file__).resolve().parents[1] / "templates"))
    app.config.update(TESTING=True, SECRET_KEY="draft-test", DATABASE_URL="sqlite:///draft", WTF_CSRF_ENABLED=False, FORM_DRAFT_TTL_DAYS=30)
    app.jinja_env.filters["trusted_html"] = lambda value: value
    app.jinja_env.filters["option_value"] = lambda value: value.get("value", "") if isinstance(value, dict) else value
    app.jinja_env.filters["option_label"] = lambda value: value.get("label", "") if isinstance(value, dict) else value
    mail_calls = []

    class MailStub:
        def dispatch_form_draft_resume(self, public_id, resume_url):
            mail_calls.append((public_id, resume_url))

    class SubmissionStub:
        def __init__(self):
            self.calls = []

        def submit_form(self, slug, config, payload, *, form_version_id=None, submission_id=None):
            self.calls.append((config, form_version_id))
            if not payload.get("note"):
                return {"ok": False, "errors": {"note": "required"}, "values": dict(payload), "submission": None, "result": None}
            with Session.begin() as db:
                row = FormSubmission(submission_id=submission_id or f"submission-{len(self.calls)}", form_slug=slug, form_name="Draft", form_version_id=form_version_id, email=payload.get("email", ""), data_json=dict(payload))
                db.add(row)
                db.flush()
                internal_id = row.id
            return {
                "ok": True, "errors": {}, "values": dict(payload),
                "submission": {"id": internal_id},
                "result": {"submission_id": submission_id or f"submission-{len(self.calls)}", "form_slug": slug, "form_title": "Draft", "pdf_filename": "", "pdf_url": "", "signed_pdf_url": None, "upload_url": "#", "verification": None},
            }

    class RepositoryStub:
        def get_by_id(self, public_id):
            with Session() as db:
                row = db.execute(select(FormSubmission).where(FormSubmission.submission_id == public_id)).scalar_one_or_none()
                return {"id": row.id} if row else None

    submission = SubmissionStub()
    app.extensions["services"] = SimpleNamespace(
        form_version_service=FormVersionService(), form_draft_service=FormDraftService(),
        mail_dispatch_service=MailStub(), submission_service=submission, submission_repository=RepositoryStub(),
    )
    monkeypatch.setattr(public_forms, "db_session_factory", lambda: Session)
    documents = Blueprint("documents", __name__)
    documents.add_url_rule("/documents", "documents_to_sign", lambda: "")
    app.register_blueprint(documents)
    app.register_blueprint(public_forms.bp)
    @app.context_processor
    def layout():
        return {"app_name": "Test", "app_base_path": "", "site_footer": SimpleNamespace(logo_position="top", layout="single", logo_html="", left_html="", right_html="", social_left_html="", social_right_html="", social_bottom_html=""), "footer_contact": SimpleNamespace(address="", phones=[], email=""), "footer_service_documents": []}
    return app, mail_calls, submission


def create_draft(client):
    page = client.get("/form/draft-form")
    token_input = re.search(
        r'<input\b(?=[^>]*\bname="form_version_token")(?=[^>]*\bvalue="([^"]+)")[^>]*>',
        page.get_data(as_text=True),
        re.DOTALL,
    )
    assert token_input is not None
    version_token = token_input.group(1)
    response = client.post("/form/draft-form/draft", data={"form_version_token": version_token, "email": "user@example.test", "note": "partial"})
    assert response.status_code == 302
    return response.headers["Location"].split("/draft/", 1)[1].split("?", 1)[0]


def test_create_hash_resume_autosave_and_no_submission(tmp_path, monkeypatch):
    Session = database(tmp_path)
    app, mail_calls, submission = draft_app(monkeypatch, Session)
    client = app.test_client()
    token = create_draft(client)
    with Session() as db:
        draft = db.execute(select(FormDraft)).scalar_one()
        assert draft.token_hash == FormDraftService.token_hash(token)
        assert draft.token_hash != token
        assert draft.form_version_id is not None
    assert mail_calls and token in mail_calls[0][1]
    resumed = client.get(f"/form/draft-form/draft/{token}")
    assert resumed.status_code == 200
    assert resumed.headers["Cache-Control"] == "no-store, private"
    assert resumed.headers["Referrer-Policy"] == "no-referrer"
    autosave = client.post(f"/form/draft-form/draft/{token}/autosave", json={"email": "user@example.test", "note": ""})
    assert autosave.status_code == 200
    autosave_without_email = client.post(
        f"/form/draft-form/draft/{token}/autosave", json={"email": "", "note": "still partial"}
    )
    assert autosave_without_email.status_code == 200
    with Session() as db:
        draft = db.execute(select(FormDraft)).scalar_one()
        assert draft.data_json["note"] == "still partial"
        assert draft.email == "user@example.test"
        assert db.execute(select(FormSubmission)).scalars().all() == []
    assert submission.calls == []


def test_wrong_expired_and_expiration_job(tmp_path, monkeypatch):
    Session = database(tmp_path)
    app, _, _ = draft_app(monkeypatch, Session)
    client = app.test_client()
    token = create_draft(client)
    assert client.get("/form/draft-form/draft/wrong-token").status_code == 404
    with Session.begin() as db:
        draft = db.execute(select(FormDraft)).scalar_one()
        draft.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert client.get(f"/form/draft-form/draft/{token}").status_code == 404
    with Session.begin() as db:
        assert db.execute(select(FormDraft.status)).scalar_one() == "expired"
        draft = db.execute(select(FormDraft)).scalar_one()
        draft.status = "active"
    with Session() as db:
        assert FormDraftService.mark_expired(db) == 1
        assert db.execute(select(FormDraft.status)).scalar_one() == "expired"


def test_submit_is_idempotent_and_keeps_original_form_version(tmp_path, monkeypatch):
    Session = database(tmp_path)
    app, _, submission = draft_app(monkeypatch, Session)
    client = app.test_client()
    token = create_draft(client)
    with Session.begin() as db:
        form = db.execute(select(Form)).scalar_one()
        old = FormVersionService().resolve_published(db, form.id)
        old_id = old.id
        new = FormVersionService().clone_to_draft(db, form, old, actor_id=None, bump="minor")
        changed = definition("Email v2")
        FormVersionService().update_definition(new, changed)
        FormVersionService().publish(db, form, new, actor_id=None)
    resumed = client.get(f"/form/draft-form/draft/{token}").get_data(as_text=True)
    assert "Email v1" in resumed and "Email v2" not in resumed
    first = client.post(f"/form/draft-form/draft/{token}/submit", data={"email": "user@example.test", "note": "complete"})
    second = client.post(f"/form/draft-form/draft/{token}/submit", data={"email": "user@example.test", "note": "complete"})
    assert first.status_code == 200
    assert second.status_code in {404, 409}
    with Session() as db:
        assert len(db.execute(select(FormSubmission)).scalars().all()) == 1
        draft = db.execute(select(FormDraft)).scalar_one()
        assert draft.status == "submitted" and draft.completed_at and draft.submission_id
    assert submission.calls[0][1] == old_id


def test_resend_is_neutral_and_rotates_token(tmp_path, monkeypatch):
    Session = database(tmp_path)
    app, mail_calls, _ = draft_app(monkeypatch, Session)
    client = app.test_client()
    old_token = create_draft(client)
    response = client.post("/form/draft-form/draft/resend", data={"draft_email": "user@example.test"}, follow_redirects=True)
    assert response.status_code == 200
    assert "Jeżeli istnieje aktywna wersja robocza" in response.get_data(as_text=True)
    new_token = mail_calls[-1][1].split("/draft/", 1)[1]
    assert new_token != old_token
    assert client.get(f"/form/draft-form/draft/{old_token}").status_code == 404
    assert client.get(f"/form/draft-form/draft/{new_token}").status_code == 200
    unknown = client.post("/form/draft-form/draft/resend", data={"draft_email": "unknown@example.test"}, follow_redirects=True)
    assert "Jeżeli istnieje aktywna wersja robocza" in unknown.get_data(as_text=True)


def test_mail_event_uses_editable_template_and_redacts_raw_token_from_log(tmp_path):
    Session = database(tmp_path)
    database_url = str(Session.kw["bind"].url)
    with Session.begin() as db:
        form = db.execute(select(Form)).scalar_one()
        draft = FormDraft(
            public_id="mail-draft", form_id=form.id, form_version_id=None, email="user@example.test",
            data_json={}, status="active", token_hash=FormDraftService.token_hash("raw-secret-token"),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1), metadata_json={},
        )
        db.add(draft)
        db.add(MailTemplate(
            form_id=form.id, name="Draft resume", template_type="form_draft_resume",
            trigger_event="form_draft_resume", subject="Resume {{ form_title }}",
            html_body='<a href="{{ draft_resume_url }}">Resume</a>', text_body="{{ draft_resume_url }}",
            is_active=True, show_process_status=False,
        ))
    delivered = []
    app = Flask(__name__)
    app.config.update(
        TESTING=True, DATABASE_URL=database_url, SMTP_HOST="smtp.test", SMTP_PORT=587,
        SMTP_USER="user", SMTP_PASSWORD="secret", MAIL_FROM="sender@example.test",
        SMTP_USE_TLS=True, SMTP_USE_SSL=False, SMTP_TIMEOUT=10,
    )
    service = MailDispatchService(smtp_sender=lambda **kwargs: delivered.append(kwargs))
    with app.app_context():
        result = service.dispatch_form_draft_resume("mail-draft", "https://example.test/draft/raw-secret-token")
    assert result.status == "sent"
    assert "raw-secret-token" in delivered[0]["html_body"]
    with Session() as db:
        log = db.execute(select(EmailLog).where(EmailLog.event_type == "form_draft_resume")).scalar_one()
        assert log.html_body == "[redacted: resumable draft link]"
        assert "raw-secret-token" not in log.html_body + log.text_body
