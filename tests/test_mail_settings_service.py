from services.mail_settings_service import MailSettingsService


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
