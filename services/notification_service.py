from __future__ import annotations

from typing import Any

from flask import current_app, render_template

from services.email_service import _send_email, send_submission_decision_email


class NotificationService:
    def __init__(self, submission_repository=None, audit_log_service=None, smtp_sender=None) -> None:
        self.submission_repository = submission_repository
        self.audit_log_service = audit_log_service
        self.smtp_sender = smtp_sender or _send_email

    def notify_event(self, event_type: str, submission: dict, form_config: dict) -> list[dict]:
        notifications = [
            item for item in form_config.get("notifications", [])
            if item.get("event") == event_type
        ]
        sent = []
        for notification in notifications:
            recipients = self._resolve_recipients(notification.get("to", []), submission)
            if not recipients:
                continue
            template = notification.get("template")
            subject = notification.get("subject") or event_type
            html_body = render_template(template, submission=submission, submission_id=submission.get("submission_id"), form_title=submission.get("form_name") or form_config.get("title", "")) if template else ""
            text_body = f"{subject}\n\nID wniosku: {submission.get('submission_id', '')}"
            self.smtp_sender(
                smtp_host=current_app.config["SMTP_HOST"],
                smtp_port=current_app.config["SMTP_PORT"],
                smtp_user=current_app.config["SMTP_USER"],
                smtp_password=current_app.config["SMTP_PASSWORD"],
                mail_from=current_app.config["MAIL_FROM"],
                to_emails=recipients,
                subject=subject,
                html_body=html_body,
                text_body=text_body,
                use_tls=current_app.config.get("SMTP_USE_TLS", True),
                use_ssl=current_app.config.get("SMTP_USE_SSL", False),
                timeout=current_app.config.get("SMTP_TIMEOUT", 30),
            )
            sent.append({"event": event_type, "to": recipients, "template": template, "subject": subject})
        return sent

    def send_decision_email(self, submission_id: str, decision: str) -> bool:
        if not self.submission_repository:
            return False
        submission = self.submission_repository.get_by_id(submission_id)
        if not submission:
            return False
        email = str(submission.get("email") or "").strip()
        if not email:
            return False
        accepted = decision in {"accepted", "TAK", "OFFICER_ACCEPTED"}
        send_submission_decision_email(
            smtp_host=current_app.config["SMTP_HOST"],
            smtp_port=current_app.config["SMTP_PORT"],
            smtp_user=current_app.config["SMTP_USER"],
            smtp_password=current_app.config["SMTP_PASSWORD"],
            mail_from=current_app.config["MAIL_FROM"],
            to_email=email,
            submission_id=submission_id,
            form_title=submission.get("form_name", ""),
            accepted=accepted,
            html_body="",
            text_body="",
            use_tls=current_app.config.get("SMTP_USE_TLS", True),
            use_ssl=current_app.config.get("SMTP_USE_SSL", False),
            timeout=current_app.config.get("SMTP_TIMEOUT", 30),
        )
        self.submission_repository.update(
            submission_id,
            {
                "officer_decision_email_sent": "Tak",
                "decision_email_sent": "Tak",
                "decision_email_sent_for": decision,
            },
        )
        return True

    def _resolve_recipients(self, configured: list[str], submission: dict) -> list[str]:
        recipients = []
        for item in configured:
            if item == "participant":
                recipients.append(str(submission.get("email") or "").strip())
            else:
                recipients.append(str(item or "").strip())
        return [item for item in recipients if item]
