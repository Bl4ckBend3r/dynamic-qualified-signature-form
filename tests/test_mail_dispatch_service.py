from datetime import datetime, timezone
import re
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from services.mail_dispatch_service import MailDispatchRequest, MailDispatchService
from database import create_session_factory
from models import EmailLog, Form, FormSubmission, FormVersion
from test_admin_panel import admin_app, create_form


def test_render_template_uses_platform_placeholders():
    rendered = MailDispatchService().render_template("Witaj {{ imiona }}", {"imiona": "Jan"})

    assert rendered == "Witaj Jan"


def test_render_subject_body_and_fallbacks():
    service = MailDispatchService()

    assert service.render_subject("Temat {{ id }}", {"id": "abc"}) == "Temat abc"
    assert service.render_body(None, {"id": "abc"}, fallback="Body {{ id }}") == "Body abc"
    assert service.render_subject(None, {}, fallback="Fallback") == "Fallback"


def test_build_footer_handles_missing_footer_and_logo():
    service = MailDispatchService()
    logo = SimpleNamespace(active=True, name="Logo", filename="logo.png")
    footer = SimpleNamespace(logo=logo, html_body="<p>Stopka</p>")

    html = service.build_footer(footer, logo_url_builder=lambda item: f"https://cdn/{item.filename}")

    assert 'src="https://cdn/logo.png"' in html
    assert "<p>Stopka</p>" in html
    assert service.build_footer(None) == ""


def test_footer_logo_source_uses_library_logo_and_never_renders_legacy_path():
    service = MailDispatchService()
    library_logo = SimpleNamespace(active=True, name="Logo stopki", filename="footer.png")
    own_logo = SimpleNamespace(
        logo=library_logo,
        logo_path="https://legacy.example/legacy.png",
        html_body="<p>Stopka</p>",
    )
    legacy_logo = SimpleNamespace(
        logo=None,
        logo_path="https://legacy.example/footer.png",
        html_body="<p>Stopka</p>",
    )
    no_logo = SimpleNamespace(
        logo=None,
        logo_path=r"C:\private\lubuskie.png",
        html_body="<p>Stopka</p>",
    )

    from_library = service.build_footer(
        own_logo,
        logo_url_builder=lambda logo: f"https://cdn.example/{logo.filename}",
    )
    from_path = service.build_footer(legacy_logo)
    without_logo = service.build_footer(no_logo)

    assert 'src="https://cdn.example/footer.png"' in from_library
    assert "legacy.png" not in from_library
    assert "<img" not in from_path
    assert "legacy.example" not in from_path
    assert "<img" not in without_logo
    assert "Lubuskie" not in without_logo


def test_footer_does_not_fall_back_to_form_or_platform_logo():
    footer = SimpleNamespace(logo=None, logo_path="", html_body="<p>Stopka bez logo</p>")

    rendered = MailDispatchService().build_footer(footer)

    assert rendered == "<p>Stopka bez logo</p>"
    assert "<img" not in rendered
    assert "platform-logo" not in rendered


@pytest.mark.parametrize("alignment", ["left", "center", "right"])
def test_build_footer_aligns_logo(alignment):
    service = MailDispatchService()
    logo = SimpleNamespace(active=True, name="Logo", filename="logo.png")
    footer = SimpleNamespace(
        logo=logo,
        logo_alignment=alignment,
        html_body="<p>Stopka</p>",
    )

    rendered = service.build_footer(
        footer,
        logo_url_builder=lambda item: f"https://cdn/{item.filename}",
    )

    assert f"text-align:{alignment}" in rendered


def _footer_with_logo(**values):
    defaults = {
        "id": 17,
        "logo": SimpleNamespace(active=True, name="Logo", filename="logo.png"),
        "logo_alignment": "left",
        "logo_position": "top",
        "logo_width": None,
        "logo_height": None,
        "html_body": "<p>Treść stopki</p>",
        "contact_html": "",
        "links": [],
        "legal_text": "",
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


@pytest.mark.parametrize(
    ("position", "logo_before_content"),
    [("top", True), ("bottom", False), ("left", True), ("right", False)],
)
def test_build_footer_places_logo_around_content(position, logo_before_content):
    rendered = MailDispatchService().build_footer(
        _footer_with_logo(logo_position=position),
        logo_url_builder=lambda logo: f"https://cdn/{logo.filename}",
    )

    assert (rendered.index("logo.png") < rendered.index("Treść stopki")) is logo_before_content
    assert ('<table role="presentation"' in rendered) is (position in {"left", "right"})


@pytest.mark.parametrize(
    ("width", "height", "expected_width", "expected_height"),
    [
        (None, None, "width:160px", "height:auto"),
        (240, None, "width:240px", "height:auto"),
        (None, 90, "width:auto", "height:90px"),
        (240, 90, "width:240px", "height:90px"),
    ],
)
def test_build_footer_applies_optional_logo_dimensions(width, height, expected_width, expected_height):
    rendered = MailDispatchService().build_footer(
        _footer_with_logo(logo_width=width, logo_height=height),
        logo_url_builder=lambda logo: f"https://cdn/{logo.filename}",
    )

    assert expected_width in rendered
    assert expected_height in rendered


def test_build_footer_replaces_inline_logo_placeholder():
    rendered = MailDispatchService().build_footer(
        _footer_with_logo(
            logo_position="inline",
            html_body="<p>Przed {{ footer_logo }} po logo.</p>",
        ),
        logo_url_builder=lambda logo: f"https://cdn/{logo.filename}",
    )

    assert "{{ footer_logo }}" not in rendered
    assert '<span class="mail-footer-logo"' in rendered
    assert rendered.index("Przed") < rendered.index("logo.png") < rendered.index("po logo")


def test_build_footer_uses_footer_cid_without_local_or_relative_source():
    rendered = MailDispatchService().build_footer(
        _footer_with_logo(logo_width=240, logo_height=90, logo_alignment="center"),
        logo_url="cid:footer-logo",
    )

    assert 'src="cid:footer-logo"' in rendered
    assert "width:240px" in rendered
    assert "height:90px" in rendered
    assert "text-align:center" in rendered
    assert "/static/" not in rendered
    assert "tmp/" not in rendered


def test_legacy_footer_logo_path_is_embedded_as_cid_when_file_exists(app, tmp_path):
    legacy_path = tmp_path / "legacy-footer.jpg"
    legacy_path.write_bytes(b"jpeg-footer")
    footer = _footer_with_logo(logo=None, logo_id=None, logo_path=str(legacy_path))
    service = MailDispatchService()

    with app.app_context():
        logo_url, inline_images = service.footer_logo_for_email(None, footer)
        rendered = service.build_footer(footer, logo_url=logo_url)

    assert logo_url == "cid:footer-logo"
    assert 'src="cid:footer-logo"' in rendered
    assert str(legacy_path) not in rendered
    assert inline_images[0]["content"] == b"jpeg-footer"
    assert inline_images[0]["mime_type"] == "image/jpeg"


def test_build_footer_falls_back_to_bottom_when_inline_placeholder_is_missing(caplog):
    rendered = MailDispatchService().build_footer(
        _footer_with_logo(logo_position="inline"),
        logo_url_builder=lambda logo: f"https://cdn/{logo.filename}",
    )

    assert rendered.index("Treść stopki") < rendered.index("logo.png")
    assert "mail_footer_inline_logo_placeholder_missing footer_id=17" in caplog.text


def test_dispatch_is_safe_without_sender_and_calls_sender_when_provided():
    service = MailDispatchService()
    request = MailDispatchRequest("EVENT", "user@example.com", "Temat", "<p>Body</p>", {"a": 1})
    calls = []

    assert service.dispatch(request) is False
    assert service.dispatch(request, sender=lambda **kwargs: calls.append(kwargs)) is True
    assert calls[0]["to"] == "user@example.com"
    assert calls[0]["event_type"] == "EVENT"


def test_dispatch_returns_false_for_missing_required_fields_or_sender_error():
    service = MailDispatchService()
    request = MailDispatchRequest("EVENT", "", "Temat", "<p>Body</p>", {})
    failing = MailDispatchRequest("EVENT", "user@example.com", "Temat", "<p>Body</p>", {})

    assert service.dispatch(request, sender=lambda **kwargs: None) is False
    assert service.dispatch(failing, sender=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))) is False


def test_build_context_for_submission_uses_submission_columns():
    service = MailDispatchService()
    submission = SimpleNamespace(
        __table__=SimpleNamespace(
            columns=[
                SimpleNamespace(name="submission_id"),
                SimpleNamespace(name="email"),
                SimpleNamespace(name="process_status"),
            ]
        ),
        data_json={"imiona": "Jan"},
        submission_id="abc",
        email="jan@example.com",
        process_status="FORM_SUBMITTED",
        pdf_filename="",
    )
    form = SimpleNamespace(name="Form", slug="form")

    context = service.build_context_for_submission(form, submission, [])

    assert context["imiona"] == "Jan"
    assert context["email"] == "jan@example.com"
    assert context["form_name"] == "Form"


def test_dispatch_raw_logs_success_and_failure(app):
    class FakeDb:
        def __init__(self):
            self.rows = []

        def add(self, row):
            self.rows.append(row)

    db = FakeDb()
    sent = []
    def sender(**kwargs):
        assert len(db.rows) == 1 and db.rows[0].status == 'pending'
        assert 'credential-secret' not in db.rows[0].html_body
        sent.append(kwargs)
    service = MailDispatchService(smtp_sender=sender)

    with app.app_context():
        result = service.dispatch_raw(
            event_type="manual",
            recipient="jan@example.com",
            subject="Temat",
            html_body="<p>Body credential-secret</p>",
            db=db,
            form=SimpleNamespace(id=1),
            submission=SimpleNamespace(id=2, submission_id="abc", access_token='credential-secret'),
        )

    assert result.status == "sent"
    assert db.rows[0].status == "sent"
    assert db.rows[0].public_submission_id == "abc"
    assert sent[0]["to_emails"] == ["jan@example.com"]


def test_dispatch_raw_missing_recipient_is_safe_and_logged():
    class FakeDb:
        def __init__(self):
            self.rows = []

        def add(self, row):
            self.rows.append(row)

    db = FakeDb()
    result = MailDispatchService().dispatch_raw(
        event_type="manual",
        recipient="",
        subject="Temat",
        html_body="<p>Body</p>",
        db=db,
        form=SimpleNamespace(id=1),
        submission=SimpleNamespace(id=2, submission_id="abc"),
    )

    assert result.status == "skipped"
    assert db.rows[0].status == "skipped"
    assert "Brak odbiorcy" in db.rows[0].error_message


def _seed_new_submission_notice(app, slug, *, enabled, recipient, submitted_at=None):
    definition = {"title": f"Formularz {slug}", "fields": [],
                  "new_submission_notification": {"enabled": enabled, "recipient_email": recipient}}
    form_id = create_form(app, slug=slug, definition_json=definition)
    factory = create_session_factory(app.config["DATABASE_URL"])
    with factory() as db:
        version = FormVersion(form_id=form_id, version_major=1, version_minor=0,
                              version_label="1.0", status="published", definition_json=definition)
        db.add(version)
        db.flush()
        submission = FormSubmission(submission_id=f"submission-{slug}", form_slug=slug,
                                    form_version_id=version.id, pesel="12345678901",
                                    process_status="FORM_SUBMITTED")
        if submitted_at is not None:
            submission.created_at = submitted_at
        db.add(submission)
        db.commit()
    return form_id, f"submission-{slug}"


def test_new_submission_notice_is_per_form_version_and_idempotent(admin_app):
    first_id, first = _seed_new_submission_notice(admin_app, "notice-first", enabled=True,
                                                   recipient="first@example.org")
    _, second = _seed_new_submission_notice(admin_app, "notice-second", enabled=True,
                                            recipient="second@example.org")
    _, disabled = _seed_new_submission_notice(admin_app, "notice-disabled", enabled=False,
                                              recipient="third@example.org")
    sent = []
    dispatcher = admin_app.extensions["services"].mail_dispatch_service
    dispatcher.smtp_sender = lambda **kwargs: sent.append(kwargs)
    factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with factory() as db:
        db.get(Form, first_id).definition_json = {"title": "Zmieniona wersja",
            "fields": [], "new_submission_notification": {"enabled": True, "recipient_email": "new@example.org"}}
        db.commit()
    with admin_app.app_context():
        assert dispatcher.dispatch_new_submission_notification(first).sent
        assert dispatcher.dispatch_new_submission_notification(second).sent
        assert dispatcher.dispatch_new_submission_notification(disabled).status == "skipped"
        assert dispatcher.dispatch_new_submission_notification(first).status == "skipped"
    assert [item["to_emails"] for item in sent] == [["first@example.org"], ["second@example.org"]]
    assert "Formularz notice-first" in sent[0]["subject"]
    assert first in sent[0]["text_body"]
    assert re.search(r"Data wpływu: \d{2}\.\d{2}\.\d{4}, \d{2}:\d{2}:\d{2}", sent[0]["text_body"])
    assert "12345678901" not in str(sent)
    assert all(item["attachments"] == [] for item in sent)
    with factory() as db:
        logs = db.execute(select(EmailLog).where(EmailLog.event_type == "new_submission_admin")).scalars().all()
        assert len(logs) == 2 and all(log.status == "sent" for log in logs)
        submission = db.execute(select(FormSubmission).where(FormSubmission.submission_id == first)).scalar_one()
        submission.process_status = "WAITING_FOR_OFFICER_DECISION"
        db.commit()
    assert admin_app.extensions["services"].submission_document_service.record_generated_document(
        submission_id=first, form_slug="notice-first", filename="extra.pdf",
        file_bytes=b"%PDF-1.4\n", document_id="extra", document_type="extra")
    with admin_app.app_context():
        assert dispatcher.dispatch_new_submission_notification(first).status == "skipped"
    assert len(sent) == 2


@pytest.mark.parametrize("submitted_at, expected", [
    (datetime(2026, 1, 15, 10, 11, 12, tzinfo=timezone.utc), "15.01.2026, 11:11:12"),
    (datetime(2026, 7, 15, 10, 11, 12, tzinfo=timezone.utc), "15.07.2026, 12:11:12"),
    (datetime(2026, 3, 29, 0, 59, 59, tzinfo=timezone.utc), "29.03.2026, 01:59:59"),
    (datetime(2026, 3, 29, 1, 0, 0, tzinfo=timezone.utc), "29.03.2026, 03:00:00"),
    (datetime(2026, 10, 25, 0, 59, 59, tzinfo=timezone.utc), "25.10.2026, 02:59:59"),
    (datetime(2026, 10, 25, 1, 0, 0, tzinfo=timezone.utc), "25.10.2026, 02:00:00"),
])
def test_new_submission_notice_uses_persisted_time_in_warsaw(admin_app, submitted_at, expected):
    _, public_id = _seed_new_submission_notice(
        admin_app, "notice-time", enabled=True, recipient="office@example.org",
        submitted_at=submitted_at,
    )
    admin_app.config["APP_TIMEZONE"] = "UTC"
    sent = []
    dispatcher = admin_app.extensions["services"].mail_dispatch_service
    dispatcher.smtp_sender = lambda **kwargs: sent.append(kwargs)

    with admin_app.app_context():
        assert dispatcher.dispatch_new_submission_notification(public_id).sent

    assert f"Data wpływu: {expected}" in sent[0]["text_body"]
    assert f"Data wpływu: {expected}" in sent[0]["html_body"]


def test_new_submission_creation_persists_full_utc_timestamp(admin_app):
    definition = {"title": "Formularz czasu", "fields": []}
    create_form(admin_app, slug="notice-created-at", definition_json=definition)
    service = admin_app.extensions["services"].submission_service

    before = datetime.now(timezone.utc)
    with admin_app.app_context():
        submission = service.create_submission(
            "notice-created-at", definition, {}, dispatch_received=False, start_workflow=False,
        )
    after = datetime.now(timezone.utc)

    factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with factory() as db:
        saved = db.execute(select(FormSubmission).where(
            FormSubmission.submission_id == submission["submission_id"]
        )).scalar_one()
        stored_at = saved.created_at.replace(tzinfo=timezone.utc) if saved.created_at.tzinfo is None else saved.created_at
    assert before <= stored_at <= after


def test_new_submission_notice_smtp_failure_keeps_submission(admin_app):
    _, public_id = _seed_new_submission_notice(admin_app, "notice-failure", enabled=True,
                                               recipient="office@example.org")
    calls = []
    def failing_sender(**kwargs):
        calls.append(kwargs)
        raise OSError("SMTP unavailable")
    dispatcher = admin_app.extensions["services"].mail_dispatch_service
    dispatcher.smtp_sender = failing_sender
    with admin_app.app_context():
        assert dispatcher.dispatch_new_submission_notification(public_id).status == "failed"
        assert dispatcher.dispatch_new_submission_notification(public_id).status == "skipped"
    assert len(calls) == 1
    factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with factory() as db:
        assert db.execute(select(FormSubmission.id).where(FormSubmission.submission_id == public_id)).scalar_one()
        log = db.execute(select(EmailLog).where(EmailLog.event_type == "new_submission_admin")).scalar_one()
        assert log.status == "failed"
