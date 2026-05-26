from __future__ import annotations

from typing import Any

from flask import current_app, render_template, render_template_string
from jinja2 import TemplateNotFound

from services.email_service import _send_email


class NotificationService:
    def __init__(self, submission_repository=None, audit_log_service=None, storage=None, smtp_sender=None) -> None:
        self.submission_repository = submission_repository
        self.audit_log_service = audit_log_service
        self.storage = storage
        self.smtp_sender = smtp_sender or _send_email

    def notify_event(
        self,
        event_type: str,
        submission: dict,
        form_config: dict,
        context_extra: dict[str, Any] | None = None,
    ) -> list[dict]:
        notifications = [
            item for item in form_config.get("notifications", [])
            if item.get("event") == event_type
        ]
        sent = []
        submission_data = self._notification_submission(submission)
        for notification in notifications:
            recipients = self._resolve_recipients(notification.get("to", []), submission_data)
            if not recipients:
                continue
            template = notification.get("template")
            subject = notification.get("subject") or event_type
            context = {
                "submission": submission_data,
                "submission_id": submission_data.get("submission_id"),
                "form_title": submission_data.get("form_name") or form_config.get("title", ""),
                "form_config": form_config,
                "event_type": event_type,
            }
            context.update(context_extra or {})
            html_body = self._render_notification_template(template, context)
            text_body = self._build_text_body(subject, context)
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

    def notify_event_once(
        self,
        event_type: str,
        submission: dict,
        form_config: dict,
        *,
        sent_field: str,
        idempotency_key: str | None = None,
        context_extra: dict[str, Any] | None = None,
    ) -> list[dict]:
        submission_id = str(submission.get("submission_id") or "").strip()
        current_submission = self.submission_repository.get_by_id(submission_id) if self.submission_repository else None
        current_submission = current_submission or submission
        sent_for_field = f"{sent_field}_for"

        if self._notification_already_sent(current_submission, sent_field, sent_for_field, idempotency_key):
            return []

        sent = self.notify_event(event_type, current_submission, form_config, context_extra=context_extra)
        if not sent:
            return []

        updates = {sent_field: "Tak"}
        if idempotency_key:
            updates[sent_for_field] = self._with_sent_key(current_submission.get(sent_for_field), idempotency_key)

        if self.submission_repository and submission_id:
            self.submission_repository.update(submission_id, updates)
        submission.update(updates)

        if self.audit_log_service:
            self.audit_log_service.log_event(
                f"{event_type}_EMAIL_SENT",
                submission_id,
                current_submission.get("form_slug", ""),
                metadata={
                    "sent_field": sent_field,
                    "idempotency_key": idempotency_key,
                    "recipients": [email for item in sent for email in item.get("to", [])],
                },
            )
        return sent

    def send_decision_email(self, submission_id: str, decision: str) -> bool:
        if not self.submission_repository:
            return False
        submission = self.submission_repository.get_by_id(submission_id)
        if not submission:
            return False
        decision_key = self._decision_key(decision)
        if self._decision_email_already_sent(submission, decision_key):
            return False
        email = str(submission.get("email") or "").strip()
        if not email:
            return False
        accepted = decision_key == "accepted"
        form_title = submission.get("form_name", "")
        template_name = "emails/decision_accepted.html" if accepted else "emails/decision_rejected.html"
        decision_context = {
            "submission_id": submission_id,
            "form_title": form_title,
            "submission": submission,
            "accepted": accepted,
            "decision": decision_key,
        }
        html_body = self._render_decision_template(template_name, decision_context)
        text_body = (
            "Dzień dobry,\n\n"
            f"wniosek dotyczący formularza \"{form_title}\" został zaakceptowany.\n\n"
            f"ID wniosku: {submission_id}\n\n"
            "Możesz przejść do podpisywania dokumentów w zakładce \"Do podpisania\".\n\n"
            "Pozdrawiamy\n"
            if accepted
            else
            "Dzień dobry,\n\n"
            f"wniosek dotyczący formularza \"{form_title}\" nie został zaakceptowany.\n\n"
            f"ID wniosku: {submission_id}\n\n"
            "W razie pytań prosimy o kontakt z urzędem.\n\n"
            "Pozdrawiamy\n"
        )
        subject = (
            "Wniosek zaakceptowany - dokumenty do podpisu"
            if accepted
            else "Wniosek nie został zaakceptowany"
        )
        self.smtp_sender(
            smtp_host=current_app.config["SMTP_HOST"],
            smtp_port=current_app.config["SMTP_PORT"],
            smtp_user=current_app.config["SMTP_USER"],
            smtp_password=current_app.config["SMTP_PASSWORD"],
            mail_from=current_app.config["MAIL_FROM"],
            to_emails=[email],
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            use_tls=current_app.config.get("SMTP_USE_TLS", True),
            use_ssl=current_app.config.get("SMTP_USE_SSL", False),
            timeout=current_app.config.get("SMTP_TIMEOUT", 30),
        )
        self.submission_repository.update(
            submission_id,
            {
                "officer_decision_email_sent": "Tak",
                "decision_email_sent": "Tak",
                "decision_email_sent_for": decision_key,
            },
        )
        if self.audit_log_service:
            self.audit_log_service.log_event(
                "DECISION_EMAIL_SENT",
                submission_id,
                submission.get("form_slug", ""),
                new_value=decision_key,
                metadata={"email": email, "accepted": accepted},
            )
        return True

    def _render_decision_template(self, template_name: str, context: dict[str, Any]) -> str:
        try:
            return render_template(template_name, **context)
        except TemplateNotFound:
            template_html = self._read_storage_template(template_name)
            if template_html:
                return render_template_string(template_html, **context)
            return self._default_decision_html_body(context)

    def _resolve_recipients(self, configured: list[str] | str, submission: dict) -> list[str]:
        if isinstance(configured, str):
            configured = [configured]
        recipients = []
        for item in configured:
            item = str(item or "").strip()
            if item == "participant":
                recipients.append(str(submission.get("email") or "").strip())
            elif item == "form_notifications":
                recipients.extend(current_app.config.get("FORM_NOTIFICATION_EMAILS", []))
            elif item.startswith("field:"):
                recipients.extend(self._split_recipients(submission.get(item.removeprefix("field:"))))
            else:
                recipients.extend(self._split_recipients(item))
        return [item for item in recipients if item]

    def _notification_submission(self, submission: dict) -> dict:
        row = submission.get("row") if isinstance(submission.get("row"), dict) else {}
        if not row:
            return dict(submission)
        outer = {key: value for key, value in submission.items() if key != "row"}
        return {**row, **outer}

    def _render_notification_template(self, template: str | None, context: dict[str, Any]) -> str:
        if not template:
            return self._default_html_body(context)

        try:
            return render_template(template, **context)
        except TemplateNotFound:
            template_html = self._read_storage_template(template)
            if template_html:
                return render_template_string(template_html, **context)
            current_app.logger.warning("Nie znaleziono szablonu maila: %s. Używam treści domyślnej.", template)
            return self._default_html_body(context)

    def _read_storage_template(self, template: str) -> str:
        if not self.storage or not hasattr(self.storage, "read_text_or_empty"):
            return ""

        normalized_path = str(template or "").replace("\\", "/").strip().strip("/")
        if not normalized_path:
            return ""

        forms_dir = current_app.config["NEXTCLOUD_FORMS_DIR"].strip("/")
        output_dir = current_app.config["NEXTCLOUD_OUTPUT_DIR"].strip("/")
        if not normalized_path.startswith((f"{forms_dir}/", f"{output_dir}/")):
            normalized_path = f"{forms_dir}/{normalized_path}"

        try:
            return self.storage.read_text_or_empty(normalized_path)
        except Exception as exc:
            current_app.logger.warning("Nie udało się odczytać szablonu maila %s: %s", normalized_path, exc)
            return ""

    def _build_text_body(self, subject: str, context: dict[str, Any]) -> str:
        if context.get("event_type") in {"AGREEMENT_SIGNED", "OFFICE_AGREEMENT_SIGNED"}:
            submission = context.get("submission") or {}
            agreement = context.get("agreement") or {}
            signed_by = context.get("signed_by") or (
                "office" if context.get("event_type") == "OFFICE_AGREEMENT_SIGNED" else "participant"
            )
            actor_text = "urząd" if signed_by == "office" else "uczestnika"
            next_step = (
                "Dokument jest gotowy do dalszej obsługi."
                if signed_by == "office"
                else "Dokument jest gotowy do podpisu po stronie urzędu."
            )
            participant_name = " ".join(
                item
                for item in [
                    str(submission.get("imie") or submission.get("imiona") or "").strip(),
                    str(submission.get("nazwisko") or "").strip(),
                ]
                if item
            )
            lines = [
                subject,
                "",
                "Dzień dobry,",
                "",
                f"w systemie formularzy poprawnie wgrano i zweryfikowano umowę podpisaną przez {actor_text}.",
                next_step,
                "",
                f"Formularz: {context.get('form_title', '')}",
                f"ID wniosku: {context.get('submission_id', '')}",
            ]
            if participant_name:
                lines.append(f"Uczestnik: {participant_name}")
            if agreement.get("training_name"):
                lines.append(f"Szkolenie: {agreement.get('training_name')}")
            if agreement.get("number"):
                lines.append(f"Numer umowy: {agreement.get('number')}")
            if context.get("signed_filename"):
                lines.append(f"Podpisany plik: {context.get('signed_filename')}")
            lines.extend(
                [
                    "",
                    "Prosimy o ręczne uzupełnienie dalszych informacji w systemie zgodnie z procedurą dla podpisanej umowy.",
                    "",
                    "Pozdrawiamy",
                ]
            )
            return "\n".join(lines)

        return (
            f"{subject}\n\n"
            f"Formularz: {context.get('form_title', '')}\n"
            f"ID wniosku: {context.get('submission_id', '')}\n"
        )

    def _default_html_body(self, context: dict[str, Any]) -> str:
        return (
            "<p>Dzień dobry,</p>"
            "<p>W systemie formularzy zapisano zgłoszenie.</p>"
            f"<p><strong>Formularz:</strong> {context.get('form_title', '')}</p>"
            f"<p><strong>ID wniosku:</strong> {context.get('submission_id', '')}</p>"
        )

    def _default_decision_html_body(self, context: dict[str, Any]) -> str:
        accepted = bool(context.get("accepted"))
        heading = "Wniosek zaakceptowany" if accepted else "Wniosek nie został zaakceptowany"
        details = (
            "Możesz przejść do podpisywania dokumentów."
            if accepted
            else "W razie pytań prosimy o kontakt z urzędem."
        )
        return (
            "<!doctype html>"
            '<html lang="pl">'
            "<body>"
            f"<h2>{heading}</h2>"
            "<p>Dzień dobry,</p>"
            f"<p>Wniosek dotyczący formularza <strong>{context.get('form_title', '')}</strong> "
            f"{'został zaakceptowany przez urzędnika.' if accepted else 'nie został zaakceptowany przez urzędnika.'}</p>"
            f"<p><strong>ID wniosku:</strong> {context.get('submission_id', '')}</p>"
            f"<p>{details}</p>"
            "<p>Pozdrawiamy</p>"
            "</body></html>"
        )

    def _decision_email_already_sent(self, submission: dict, decision_key: str) -> bool:
        sent_values = {
            str(submission.get("decision_email_sent") or "").strip().lower(),
            str(submission.get("officer_decision_email_sent") or "").strip().lower(),
        }
        if not (sent_values & {"tak", "yes", "true", "1"}):
            return False

        sent_for = str(submission.get("decision_email_sent_for") or "").strip()
        if not sent_for:
            return True
        return self._decision_key(sent_for) == decision_key

    def _decision_key(self, decision: str) -> str:
        normalized = str(decision or "").strip().lower()
        if normalized in {"accepted", "tak", "officer_accepted"}:
            return "accepted"
        if normalized in {"rejected", "nie", "officer_rejected"}:
            return "rejected"
        return normalized

    def _notification_already_sent(
        self,
        submission: dict,
        sent_field: str,
        sent_for_field: str,
        idempotency_key: str | None,
    ) -> bool:
        sent = str(submission.get(sent_field) or "").strip().lower() in {"tak", "yes", "true", "1"}
        if not sent:
            return False
        if not idempotency_key:
            return True

        sent_keys = self._sent_keys(submission.get(sent_for_field))
        if not sent_keys:
            return True
        return idempotency_key in sent_keys

    def _with_sent_key(self, raw_value: Any, key: str) -> str:
        keys = self._sent_keys(raw_value)
        if key not in keys:
            keys.append(key)
        return ",".join(keys)

    def _sent_keys(self, raw_value: Any) -> list[str]:
        raw = str(raw_value or "").strip()
        if not raw:
            return []
        return [item.strip() for item in raw.replace(";", ",").split(",") if item.strip()]

    def _split_recipients(self, raw_value: Any) -> list[str]:
        raw = str(raw_value or "").strip()
        if not raw:
            return []
        return [item.strip() for item in raw.replace(";", ",").split(",") if item.strip()]
