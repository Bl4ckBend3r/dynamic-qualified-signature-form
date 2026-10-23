from services.notification_service import NotificationService


def test_legacy_form_notifications_are_disabled(app):
    sent = []
    with app.app_context():
        result = NotificationService(smtp_sender=lambda **kwargs: sent.append(kwargs)).notify_event(
            "FORM_SUBMITTED",
            {"submission_id": "abc", "email": "person@example.com"},
            {"notifications": [{"event": "FORM_SUBMITTED", "subject": "Stary mail"}]},
        )
    assert result == []
    assert sent == []


def test_decision_email_is_disabled(app):
    sent = []
    with app.app_context():
        result = NotificationService(smtp_sender=lambda **kwargs: sent.append(kwargs)).send_decision_email("abc", "TAK")
    assert result is False
    assert sent == []


def test_workflow_notification_once_is_disabled(app):
    sent = []
    with app.app_context():
        result = NotificationService(smtp_sender=lambda **kwargs: sent.append(kwargs)).notify_event_once(
            "AGREEMENT_SIGNED",
            {"submission_id": "abc", "email": "person@example.com"},
            {},
            sent_field="agreement_success_email_sent",
        )
    assert result == []
    assert sent == []
