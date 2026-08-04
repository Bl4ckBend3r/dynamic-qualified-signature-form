from types import SimpleNamespace
from datetime import datetime, timezone
from pathlib import Path
import importlib

from flask import Flask
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Boolean, Column, Integer, MetaData, Table, Text, create_engine, inspect
from sqlalchemy.orm import Session

from form_loader import normalize_form_definition, validate_submission
from models import Base, EmailLog, Form, FormSubmission, MailTemplate
from services.admin_submission_service import build_status_filter_options, paginate_submissions
from services.mail_dispatch_service import MailDispatchService
from services.mail_template_service import build_mail_context
from services.process_instruction_service import build_process_instruction_view


def _phone_definition(field_type="tel", name="telefon"):
    return normalize_form_definition({"title": "Telefon", "fields": [{"type": field_type, "name": name, "label": "Telefon"}]})


def test_polish_phone_validation_accepts_supported_formats_and_rejects_invalid_values():
    for value in ("123456789", "123 456 789", "+48 123 456 789", "+48123456789"):
        assert validate_submission(_phone_definition(), {"telefon": value}) == {}
    for value in ("12345", "telefon", "+49 123 456 789", "12-345-6789"):
        assert validate_submission(_phone_definition(), {"telefon": value})["telefon"] == "Podaj poprawny numer telefonu."


def test_phone_aliases_include_phone_type_and_generic_telefon_name():
    assert validate_submission(_phone_definition("phone"), {"telefon": "123 456 789"}) == {}
    assert validate_submission(_phone_definition("text"), {"telefon": "abc"})["telefon"] == "Podaj poprawny numer telefonu."


def test_status_filter_comes_from_form_workflow_and_pagination_is_stable():
    form = Form(slug="program", name="Program", definition_json={"workflow": {"steps": [{"id": "review", "status": "CUSTOM_REVIEW", "label": "Ocena formalna"}]}})
    options = build_status_filter_options(form, [])
    assert {item["value"]: item["label"] for item in options}["CUSTOM_REVIEW"] == "Ocena formalna"
    rows = [SimpleNamespace(id=index) for index in range(55)]
    page, metadata = paginate_submissions(rows, 2, 50)
    assert len(page) == 5
    assert metadata == {"page": 2, "per_page": 50, "total": 55, "pages": 2, "has_previous": True, "has_next": False}


def test_instruction_exposes_current_description_action_and_next_stage():
    view = build_process_instruction_view(
        "DECLARATION_WAITING_FOR_SIGNATURE",
        instruction_config={"stages": [
            {"key": "declaration", "label": "Deklaracja oczekuje na podpis", "description": "Pobierz i podpisz.", "next_action": "Wgraj podpisany plik.", "status_codes": ["DECLARATION_WAITING_FOR_SIGNATURE"]},
            {"key": "training", "label": "Wybór szkoleń", "status_codes": ["TRAINING_SELECTION_OPEN"]},
        ]},
    )["instruction"]
    assert view["current_stage_label"] == "Deklaracja oczekuje na podpis"
    assert view["current_stage_description"] == "Pobierz i podpisz."
    assert view["next_action"] == "Wgraj podpisany plik."
    assert view["next_stage_label"] == "Wybór szkoleń"


def test_mail_context_has_polish_status_and_stage_variables():
    form = Form(slug="program", name="Program", definition_json={"workflow": {"steps": [{"id": "review", "label": "Ocena urzędnika"}]}})
    submission = FormSubmission(submission_id="abc", form_slug="program", process_status="OFFICER_ACCEPTED", workflow_step="review", data_json={})
    context = build_mail_context(form, submission)
    assert context["process_status"] == "OFFICER_ACCEPTED"
    assert context["process_status_label"] == "Wniosek zaakceptowany"
    assert context["current_stage"] == "review"
    assert context["current_stage_label"] == "Ocena urzędnika"


def test_sent_correspondence_stores_rendered_body_and_can_hide_status():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    app = Flask(__name__)
    app.config.update(SMTP_HOST="smtp.example", SMTP_PORT=587, MAIL_FROM="system@example.com")
    sent = []
    with app.test_request_context("/"):
        with Session(engine) as db:
            form = Form(slug="program", name="Program", definition_json={})
            db.add(form)
            db.flush()
            submission = FormSubmission(submission_id="abc", form_slug="program", email="user@example.com", process_status="OFFICER_ACCEPTED", data_json={})
            db.add(submission)
            db.flush()
            template = MailTemplate(form_id=form.id, name="Akceptacja", subject="Status", html_body="<p>{{ process_status }} {{ process_status_label }}</p>", text_body="{{ process_status }} {{ process_status_label }}", show_process_status=False)
            db.add(template)
            db.flush()
            service = MailDispatchService(smtp_sender=lambda **kwargs: sent.append(kwargs))
            result = service.dispatch_to_submission(db=db, form=form, submission=submission, template=template, event_type="manual")
            db.commit()
            log = db.query(EmailLog).one()
            assert result.sent
            assert "OFFICER_ACCEPTED" not in sent[0]["html_body"]
            assert log.html_body == sent[0]["html_body"]
            assert log.text_body == sent[0]["text_body"]


def test_correction_acceptance_mail_is_configured_logged_and_idempotent(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'correction-mail.db'}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        form = Form(slug="program", name="Program", definition_json={"workflow": {"send_email_notifications": True}})
        db.add(form)
        db.flush()
        submission = FormSubmission(
            submission_id="corrected-1", form_slug="program", email="user@example.com",
            process_status="OFFICER_ACCEPTED", correction_completed_at=datetime.now(timezone.utc), data_json={},
        )
        db.add(submission)
        db.add(MailTemplate(form_id=form.id, name="Korekta zaakceptowana", template_type="correction_accepted", subject="Korekta {{ submission_id }}", html_body="<p>Zaakceptowano</p>"))
        db.commit()
    sent = []
    app = Flask(__name__)
    app.config.update(DATABASE_URL=database_url, SMTP_HOST="smtp.example", SMTP_PORT=587, MAIL_FROM="system@example.com")
    repository = SimpleNamespace(list_submission_files=lambda _submission_id: [])
    service = MailDispatchService(submission_repository=repository, smtp_sender=lambda **kwargs: sent.append(kwargs))
    with app.test_request_context("/"):
        assert service.dispatch_correction_accepted("corrected-1").sent
        assert not service.dispatch_correction_accepted("corrected-1").sent
    with Session(engine) as db:
        assert db.query(EmailLog).filter_by(event_type="correction_accepted", status="sent").count() == 1
    assert len(sent) == 1


def test_admin_submission_views_expose_filter_quick_actions_and_correspondence():
    listing = Path("templates/admin/submissions/list.html").read_text(encoding="utf-8")
    detail = Path("templates/admin/submissions/detail.html").read_text(encoding="utf-8")
    for label in ("Telefon", "Etap workflow", "Zaakceptuj", "Odrzuć", "Wyślij do poprawy"):
        assert label in listing
    assert "status_options" in listing
    assert "Korespondencja" in detail
    assert "Pokaż treść" in detail


def test_declaration_branding_training_redirect_and_agreement_authoring_order_are_documented():
    declaration = Path("templates/declaration_template.html").read_text(encoding="utf-8")
    documents_route = Path("routes/documents.py").read_text(encoding="utf-8")
    form_editor = Path("templates/admin/forms/edit.html").read_text(encoding="utf-8")
    assert "{% if pdf_image_url %}" in declaration
    assert "pdf_image_alignment" in declaration
    assert "Logo formularza" in declaration
    assert '"documents.documents_to_sign"' in documents_route
    assert "Wybór szkoleń został zapisany. Możesz przejść do umów." in documents_route
    assert "najpierw numer paragrafu" in form_editor
    assert "pod nim nazwę sekcji" in form_editor


def test_correspondence_migration_adds_columns_without_touching_existing_rows(monkeypatch):
    migration = importlib.import_module("migrations.versions.20260804_0029_email_correspondence_and_status_visibility")
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    logs = Table("email_logs", metadata, Column("id", Integer, primary_key=True))
    templates = Table("mail_templates", metadata, Column("id", Integer, primary_key=True))
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(logs.insert().values(id=7))
        connection.execute(templates.insert().values(id=9))
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        migration.upgrade()
        assert connection.execute(Table("email_logs", MetaData(), autoload_with=connection).select()).mappings().one()["id"] == 7
        assert connection.execute(Table("mail_templates", MetaData(), autoload_with=connection).select()).mappings().one()["id"] == 9
        assert {"html_body", "text_body"} <= {item["name"] for item in inspect(connection).get_columns("email_logs")}
        assert "show_process_status" in {item["name"] for item in inspect(connection).get_columns("mail_templates")}
