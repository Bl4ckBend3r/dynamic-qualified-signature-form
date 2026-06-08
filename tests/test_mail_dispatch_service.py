from types import SimpleNamespace

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
