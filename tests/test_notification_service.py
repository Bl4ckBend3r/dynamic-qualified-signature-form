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
