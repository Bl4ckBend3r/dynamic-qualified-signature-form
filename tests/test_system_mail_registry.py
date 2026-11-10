from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from models import Base, MailTemplate, PlatformMailTemplate
from services.mail_dispatch_service import MailDispatchService
from services.mail_template_service import render_template_text
from services.system_mail_registry import (
    CONFIRMATION_EVENTS,
    DEFAULT_TEMPLATE_FILES,
    DEFAULT_TEMPLATE_ROOT,
    FORM_MAIL_TEMPLATE_TYPES,
    SYSTEM_MAIL_BY_ID,
    SYSTEM_MAIL_TYPES,
    SystemMailTemplateError,
    TECHNICAL_MAIL_TYPES,
    load_default_template,
    system_mail_variables,
)


def test_system_mail_ids_are_unique_and_distinct_from_technical_mail():
    ids = [item.id for item in SYSTEM_MAIL_TYPES]
    assert len(ids) == len(set(ids)) == 11
    assert set(ids) == {
        "submission_received", "auto_rejected_by_condition", "returned_for_correction",
        "form_draft_resume", "new_submission_admin", "document_received",
        "stage_rollback", "agreement_signed_by_office", "agreement_rejected_by_office",
        "training_attendance_invitation", "training_survey_invitation",
    }
    assert set(ids) == set(SYSTEM_MAIL_BY_ID)
    assert {item.source_policy for item in SYSTEM_MAIL_TYPES} == {
        "platform_required", "form_platform_fallback", "form_fallback", "dynamic",
    }
    assert "smtp_test" in TECHNICAL_MAIL_TYPES
    assert not set(ids) & set(TECHNICAL_MAIL_TYPES)
    assert CONFIRMATION_EVENTS == ("submission_created", "submission_received")
    assert len(FORM_MAIL_TEMPLATE_TYPES) == len(set(FORM_MAIL_TEMPLATE_TYPES))


def test_system_source_priority_preserves_form_and_platform_fallbacks():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    dispatcher = MailDispatchService()
    form = SimpleNamespace(id=1)
    with Session(engine) as db:
        platform = PlatformMailTemplate(
            template_type="auto_rejected_by_condition", name="global",
            subject="Global", is_active=True)
        form_template = MailTemplate(
            form_id=1, name="form", template_type="auto_rejected_by_condition",
            subject="Form", is_active=True)
        db.add_all([platform, form_template])
        db.flush()
        assert dispatcher.resolve_system_template(
            db, event_type="auto_rejected_by_condition", form=form) is form_template
        form_template.is_active = False
        db.flush()
        assert dispatcher.resolve_system_template(
            db, event_type="auto_rejected_by_condition", form=form) is platform
        platform.is_active = False
        db.flush()
        assert dispatcher.resolve_system_template(
            db, event_type="auto_rejected_by_condition", form=form).subject == (
                "Zgłoszenie {{ submission_id }} nie spełnia warunków udziału")

        receipt = PlatformMailTemplate(
            template_type="submission_received", name="receipt", is_active=False)
        db.add(receipt)
        db.flush()
        assert dispatcher.resolve_system_template(db, event_type="submission_received", form=form) is receipt
        db.delete(receipt)
        db.flush()
        assert dispatcher.resolve_system_template(
            db, event_type="submission_received", form=form).subject == (
                "Potwierdzenie otrzymania zgłoszenia")
        assert dispatcher.resolve_system_template(
            db, event_type="document_received", form=form).subject == "Wpłynął dokument — {{ form_name }}"
    engine.dispose()


def test_form_override_matching_preserves_legacy_trigger_rules():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    dispatcher = MailDispatchService()
    form = SimpleNamespace(id=1)
    with Session(engine) as db:
        draft = MailTemplate(
            form_id=1, name="draft", template_type="custom",
            trigger_event="form_draft_resume", is_active=True)
        status = MailTemplate(
            form_id=1, name="agreement", template_type="custom",
            trigger_status="AGREEMENT_SIGNED_BY_OFFICE", is_active=True)
        db.add_all([draft, status])
        db.flush()
        assert dispatcher.resolve_system_template(db, event_type="form_draft_resume", form=form) is draft
        assert dispatcher.resolve_system_template(db, event_type="agreement_signed_by_office", form=form) is status
        assert dispatcher.resolve_system_template(db, event_type="stage_rollback", form=form).subject == (
            "Cofnięto etap zgłoszenia {{ submission_id }}")
    engine.dispose()


def test_every_registered_type_has_exactly_one_utf8_template_set():
    assert {path.name for path in DEFAULT_TEMPLATE_ROOT.iterdir() if path.is_dir()} == set(SYSTEM_MAIL_BY_ID)
    for mail_type in SYSTEM_MAIL_TYPES:
        directory = DEFAULT_TEMPLATE_ROOT / mail_type.id
        assert {path.name for path in directory.iterdir()} == set(DEFAULT_TEMPLATE_FILES)
        template = load_default_template(mail_type.id)
        assert template.subject and template.html_body and template.text_body
    assert "smtp_test" not in SYSTEM_MAIL_BY_ID
    assert "submission_created" not in SYSTEM_MAIL_BY_ID
    assert "Zgłoszenie" in load_default_template("auto_rejected_by_condition").subject
    assert "Twojego zgłoszenia" in load_default_template("auto_rejected_by_condition").html_body


def test_file_templates_render_existing_dynamic_text_without_reinterpreting_values():
    attendance = load_default_template("training_attendance_invitation")
    attendance_context = {
        "session_name": "A&B", "session_name_lines": ["A&B"], "mail_message": "",
    }
    assert render_template_text(attendance.subject, attendance_context) == "Potwierdź obecność — A&B"
    assert render_template_text(attendance.html_body, attendance_context, escape_html=True) == (
        "<p>Zapraszamy do potwierdzenia obecności na szkoleniu: A&amp;B. "
        "To zaproszenie dotyczy Twojego zgłoszenia wskazanego poniżej.</p>")
    survey = load_default_template("training_survey_invitation")
    survey_context = {
        "survey_name": "Ankieta", "survey_description": "Pierwsza\nDruga",
        "survey_description_lines": ["Pierwsza", "Druga"], "mail_message": "",
    }
    assert render_template_text(survey.html_body, survey_context, escape_html=True) == (
        "<p>Zapraszamy do wypełnienia ankiety dotyczącej szkolenia powiązanego "
        "z Twoim zgłoszeniem.<br>Pierwsza<br>Druga</p>")
    assert render_template_text(survey.text_body, survey_context) == (
        "Zapraszamy do wypełnienia ankiety dotyczącej szkolenia powiązanego "
        "z Twoim zgłoszeniem.\nPierwsza\nDruga")
    correction = load_default_template("returned_for_correction")
    assert render_template_text(correction.html_body, {"correction_reason": "<br>",
        "correction_message": "Uzupełnij", "correction_clear_info": "Bez zmian",
        "correction_url": "https://example.org/correct"}, escape_html=True).count("&lt;br&gt;") == 1


def test_missing_or_unsafe_default_file_raises_controlled_error(tmp_path, monkeypatch):
    import services.system_mail_registry as registry

    directory = tmp_path / "document_received"
    directory.mkdir()
    (directory / "subject.txt").write_text("Temat", encoding="utf-8")
    (directory / "body.html").write_text("<p>Treść</p>", encoding="utf-8")
    monkeypatch.setattr(registry, "DEFAULT_TEMPLATE_ROOT", tmp_path)
    with pytest.raises(SystemMailTemplateError, match="body.txt"):
        load_default_template("document_received")
    with pytest.raises(SystemMailTemplateError, match="Unknown"):
        load_default_template("../document_received")
    (directory / "body.txt").write_bytes(b"\xff\xfe")
    with pytest.raises(SystemMailTemplateError, match="body.txt"):
        load_default_template("document_received")


def test_variable_catalog_tracks_registered_events_without_internal_data():
    for mail_type in SYSTEM_MAIL_TYPES:
        variables = system_mail_variables(mail_type.id)
        names = [variable.name for variable in variables]
        assert len(names) == len(set(names))
        assert all(variable.description and variable.syntax == "{{ " + variable.name + " }}"
                   for variable in variables)
        assert not {"access_token", "smtp_password", "data_json", "submission"} & set(names)
    assert "qualification_message" in {v.name for v in system_mail_variables("auto_rejected_by_condition")}
    assert "qualification_message" not in {v.name for v in system_mail_variables("document_received")}
    assert "draft_resume_url" in {v.name for v in system_mail_variables("form_draft_resume")}
    assert "draft_resume_url" not in {v.name for v in system_mail_variables("submission_received")}


def test_active_platform_override_is_used_for_dynamic_types_and_form_still_wins():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    dispatcher = MailDispatchService()
    form = SimpleNamespace(id=1)
    with Session(engine) as db:
        platform = PlatformMailTemplate(template_type="training_survey_invitation", name="Ankieta",
                                        subject="Ankieta {{ survey_name }}", html_body="<p>Nowa</p>",
                                        text_body="Nowa", is_active=True)
        form_template = MailTemplate(form_id=1, name="Form", template_type="stage_rollback",
                                     subject="Form", is_active=True)
        stage_platform = PlatformMailTemplate(template_type="stage_rollback", name="Platform",
                                              subject="Platform", is_active=True)
        db.add_all([platform, form_template, stage_platform])
        db.flush()
        assert dispatcher.resolve_system_template(db, event_type="training_survey_invitation", form=form) is platform
        assert dispatcher.resolve_system_template(db, event_type="stage_rollback", form=form) is form_template
        form_template.is_active = False
        db.flush()
        assert dispatcher.resolve_system_template(db, event_type="stage_rollback", form=form) is stage_platform
    engine.dispose()


def test_all_registered_types_accept_a_platform_override():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    dispatcher = MailDispatchService()
    with Session(engine) as db:
        for mail_type in SYSTEM_MAIL_TYPES:
            db.add(PlatformMailTemplate(template_type=mail_type.id, name=mail_type.name,
                                        subject="Zapis administratora", html_body="<p>Treść</p>",
                                        text_body="Treść", is_active=True))
        db.flush()
        for mail_type in SYSTEM_MAIL_TYPES:
            assert dispatcher.resolve_system_template(
                db, event_type=mail_type.id).subject == "Zapis administratora"
    engine.dispose()
