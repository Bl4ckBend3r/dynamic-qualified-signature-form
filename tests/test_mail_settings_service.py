from services.mail_settings_service import MailSettingsService
from services.email_service import _send_email


def test_smtp_connection_test_authenticates_without_sending(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            calls.append(("ehlo",))

        def starttls(self, context):
            calls.append(("starttls", bool(context)))

        def login(self, user, password):
            calls.append(("login", user, password))

    monkeypatch.setattr("services.mail_settings_service.smtplib.SMTP", FakeSMTP)
    service = MailSettingsService("test-secret")

    service.test_connection(
        {
            "smtp_host": "smtp.test",
            "smtp_port": 587,
            "smtp_user": "user",
            "smtp_password": "secret",
            "use_tls": True,
            "use_ssl": False,
            "timeout": 12,
        }
    )

    assert calls[0] == ("connect", "smtp.test", 587, 12)
    assert ("starttls", True) in calls
    assert ("login", "user", "secret") in calls
    assert all(call[0] not in {"send", "sendmail", "send_message"} for call in calls)


def test_password_encryption_round_trip_and_public_config_never_exposes_password():
    service = MailSettingsService("test-secret")
    encrypted = service.encrypt_password("smtp-secret")

    assert encrypted
    assert encrypted != "smtp-secret"
    assert service.decrypt_password(encrypted) == "smtp-secret"
    assert "password" not in service.public_config({"host": "smtp.test", "password": "smtp-secret"})


def test_mail_layout_logo_defaults_and_bounds_are_safe():
    service = MailSettingsService("test-secret")

    defaults = service.layout_from_form({})
    bounded = service.layout_from_form({
        "layout_logo_position": "invalid",
        "layout_logo_alignment": "invalid",
        "layout_logo_height_px": "999",
    })

    assert defaults["logo_position"] == "none"
    assert defaults["logo_alignment"] == "center"
    assert defaults["logo_height_px"] == 64
    assert bounded["logo_position"] == "none"
    assert bounded["logo_alignment"] == "center"
    assert bounded["logo_height_px"] == 200


def test_email_sender_embeds_logo_as_inline_cid_attachment(monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr("services.email_service.smtplib.SMTP", FakeSMTP)
    _send_email(
        smtp_host="smtp.test",
        smtp_port=25,
        smtp_user="",
        smtp_password="",
        mail_from="sender@example.com",
        to_emails=["user@example.com"],
        subject="Subject",
        html_body='<p>Body</p><img src="cid:platform-logo-1" alt="">',
        text_body="Body",
        use_tls=False,
        inline_images=[{
            "cid": "platform-logo-1",
            "content": b"png-content",
            "mime_type": "image/png",
            "filename": "logo.png",
        }],
    )

    message = sent[0]
    image = next(part for part in message.walk() if part.get_content_maintype() == "image")
    assert image["Content-ID"] == "<platform-logo-1>"
    assert image.get_content_disposition() == "inline"
    assert image.get_payload(decode=True) == b"png-content"
