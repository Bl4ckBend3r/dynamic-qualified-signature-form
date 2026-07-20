from types import SimpleNamespace

import pytest

from services.mail_dispatch_service import MailDispatchRequest, MailDispatchService


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
    service = MailDispatchService(smtp_sender=lambda **kwargs: sent.append(kwargs))

    with app.app_context():
        result = service.dispatch_raw(
            event_type="manual",
            recipient="jan@example.com",
            subject="Temat",
            html_body="<p>Body</p>",
            db=db,
            form=SimpleNamespace(id=1),
            submission=SimpleNamespace(id=2, submission_id="abc"),
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
