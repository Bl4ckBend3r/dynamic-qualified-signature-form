from services.notification_service import NotificationService


def test_notify_event_uses_configured_template_and_mocked_smtp(app):
    sent = []

    def fake_sender(**kwargs):
        sent.append(kwargs)

    config = {
        "title": "Form",
        "notifications": [
            {
                "event": "FORM_SUBMITTED",
                "to": ["participant"],
                "template": "emails/decision_accepted.html",
                "subject": "OK",
            }
        ],
    }
    submission = {"submission_id": "abc", "email": "person@example.com", "form_name": "Form"}

    with app.app_context():
        result = NotificationService(smtp_sender=fake_sender).notify_event("FORM_SUBMITTED", submission, config)

    assert result[0]["to"] == ["person@example.com"]
    assert sent[0]["subject"] == "OK"


def test_send_decision_email_has_non_empty_html_and_text(app):
    sent = []
    submission = {
        "submission_id": "abc",
        "form_slug": "formularz_zgloszeniowy",
        "email": "person@example.com",
        "form_name": "Form",
    }
    app.testing_storage.csv_rows = [submission]

    def fake_sender(**kwargs):
        sent.append(kwargs)

    with app.app_context():
        result = NotificationService(
            submission_repository=app.extensions["submission_repository"],
            smtp_sender=fake_sender,
        ).send_decision_email("abc", "TAK")

    assert result is True
    assert sent
    assert sent[0]["html_body"].strip()
    assert sent[0]["text_body"].strip()


def test_send_decision_email_is_idempotent_for_same_decision(app):
    sent = []
    submission = {
        "submission_id": "abc",
        "form_slug": "formularz_zgloszeniowy",
        "email": "person@example.com",
        "form_name": "Form",
    }
    app.testing_storage.csv_rows = [submission]

    def fake_sender(**kwargs):
        sent.append(kwargs)

    with app.app_context():
        service = NotificationService(
            submission_repository=app.extensions["submission_repository"],
            smtp_sender=fake_sender,
        )
        first_result = service.send_decision_email("abc", "TAK")
        second_result = service.send_decision_email("abc", "TAK")

    assert first_result is True
    assert second_result is False
    assert len(sent) == 1
    assert app.testing_storage.csv_rows[0]["decision_email_sent"] == "Tak"
    assert app.testing_storage.csv_rows[0]["decision_email_sent_for"] == "accepted"


def test_notify_event_renders_template_from_storage(app):
    sent = []
    template_path = "Formularze/Template/Mail/potwierdzenie.html"
    app.testing_storage.direct_files[template_path] = (
        b"<p>Potwierdzenie {{ submission_id }} dla {{ form_title }}</p>"
    )

    def fake_sender(**kwargs):
        sent.append(kwargs)

    config = {
        "title": "Formularz",
        "notifications": [
            {
                "event": "FORM_SUBMITTED",
                "to": ["participant"],
                "template": "Template/Mail/potwierdzenie.html",
                "subject": "Potwierdzenie",
            }
        ],
    }
    submission = {
        "submission_id": "abc",
        "email": "person@example.com",
        "form_name": "Formularz",
    }

    with app.app_context():
        result = NotificationService(
            storage=app.testing_storage,
            smtp_sender=fake_sender,
        ).notify_event("FORM_SUBMITTED", submission, config)

    assert result[0]["template"] == "Template/Mail/potwierdzenie.html"
    assert "Potwierdzenie abc dla Formularz" in sent[0]["html_body"]
    assert sent[0]["text_body"].strip()


def test_notify_event_once_sends_participant_agreement_signed_email_once(app):
    sent = []
    template_path = "Formularze/Template/Mail/agreement_signed.html"
    app.testing_storage.direct_files[template_path] = (
        b"<p>Umowa uczestnika {{ submission_id }} {{ signed_filename }}</p>"
    )
    submission = {
        "submission_id": "abc",
        "form_slug": "formularz_zgloszeniowy",
        "email": "person@example.com",
        "form_name": "Form",
        "agreement_signature_valid": "Tak",
    }
    app.testing_storage.csv_rows = [submission]

    def fake_sender(**kwargs):
        sent.append(kwargs)

    config = {
        "title": "Form",
        "notifications": [
            {
                "event": "AGREEMENT_SIGNED",
                "to": ["participant"],
                "template": "Template/Mail/agreement_signed.html",
                "subject": "Umowa podpisana przez uczestnika",
            }
        ],
    }

    with app.app_context():
        service = NotificationService(
            submission_repository=app.extensions["submission_repository"],
            storage=app.testing_storage,
            smtp_sender=fake_sender,
        )
        first_result = service.notify_event_once(
            "AGREEMENT_SIGNED",
            submission,
            config,
            sent_field="agreement_success_email_sent",
            idempotency_key="all",
            context_extra={"signed_filename": "umowa-signed.pdf", "signed_by": "participant"},
        )
        second_result = service.notify_event_once(
            "AGREEMENT_SIGNED",
            submission,
            config,
            sent_field="agreement_success_email_sent",
            idempotency_key="all",
            context_extra={"signed_filename": "umowa-signed.pdf", "signed_by": "participant"},
        )

    assert first_result
    assert second_result == []
    assert len(sent) == 1
    assert sent[0]["to_emails"] == ["person@example.com"]
    assert "Umowa uczestnika abc umowa-signed.pdf" in sent[0]["html_body"]
    assert sent[0]["html_body"].strip()
    assert sent[0]["text_body"].strip()
    assert app.testing_storage.csv_rows[0]["agreement_success_email_sent"] == "Tak"
    assert app.testing_storage.csv_rows[0]["agreement_success_email_sent_for"] == "all"
