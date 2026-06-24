from __future__ import annotations

from dataclasses import dataclass
from html import escape
from types import SimpleNamespace
from typing import Any

from flask import current_app, render_template
from jinja2 import TemplateNotFound

from services.admin_mail_context_service import build_mail_context, mail_template_type_score
from services.mail_template_service import render_platform_mail_html, render_platform_mail_text, render_template_text


@dataclass(frozen=True)
class MailDispatchRequest:
    event_type: str
    recipient: str
    subject: str
    body: str
    context: dict[str, Any]


@dataclass(frozen=True)
class MailDispatchResult:
    status: str
    recipient: str = ""
    subject: str = ""
    error_message: str = ""
    log: Any | None = None

    @property
    def sent(self) -> bool:
        return self.status == "sent"


class MailDispatchService:
    """Central facade for rendering, sending and logging application e-mails."""

    def __init__(
        self,
        *,
        notification_service=None,
        submission_repository=None,
        audit_log_service=None,
        smtp_sender=None,
    ) -> None:
        self.notification_service = notification_service
        self.submission_repository = submission_repository
        self.audit_log_service = audit_log_service
        self.smtp_sender = smtp_sender

    def render_template(self, template: str | None, context: dict[str, Any] | None = None) -> str:
        if not template:
            return ""
        return render_template_text(template, context or {})

    def render_subject(self, template: str | None, context: dict[str, Any] | None = None, fallback: str = "") -> str:
        return self.render_template(template or fallback, context)

    def render_body(self, template: str | None, context: dict[str, Any] | None = None, fallback: str = "") -> str:
        return self.render_template(template or fallback, context)

    def build_context_for_submission(
        self,
        form,
        submission=None,
        files: list | None = None,
        **builders,
    ) -> dict[str, Any]:
        return build_mail_context(form, submission, files or [], **builders)

    def build_footer(self, footer=None, logo_url_builder=None) -> str:
        if not footer:
            return ""
        parts = []
        logo = getattr(footer, "logo", None)
        if logo and getattr(logo, "active", False) and logo_url_builder:
            logo_url = logo_url_builder(logo)
            parts.append(
                '<div style="margin-bottom:16px;">'
                f'<img src="{escape(str(logo_url))}" alt="{escape(str(getattr(logo, "name", "")))}" '
                'style="display:block;max-width:180px;max-height:80px;width:auto;height:auto;">'
                "</div>"
            )
        html_body = getattr(footer, "html_body", "") or ""
        if html_body:
            parts.append(html_body)
        return "\n".join(parts)

    def select_template(self, templates: list[Any], submission=None, event_type: str | None = None):
        if not templates:
            return None
        if submission is None and not event_type:
            return templates[0]
        candidates = []
        for template in templates:
            if getattr(template, "is_active", True) is False:
                continue
            if event_type and getattr(template, "trigger_event", "") and template.trigger_event != event_type:
                continue
            if submission is not None:
                if getattr(template, "trigger_status", "") and template.trigger_status != getattr(submission, "process_status", ""):
                    continue
                if getattr(template, "trigger_decision", "") and template.trigger_decision != getattr(submission, "officer_decision", ""):
                    continue
                type_score = self.mail_template_type_score(template, submission)
            else:
                type_score = 0
            if not any(
                [
                    getattr(template, "trigger_event", ""),
                    getattr(template, "trigger_status", ""),
                    getattr(template, "trigger_decision", ""),
                    getattr(template, "is_default_for_status", False),
                    type_score,
                ]
            ):
                continue
            score = 0
            score += 8 if event_type and getattr(template, "trigger_event", "") == event_type else 0
            score += 4 if submission is not None and getattr(template, "trigger_decision", "") == getattr(submission, "officer_decision", "") else 0
            score += 2 if submission is not None and getattr(template, "trigger_status", "") == getattr(submission, "process_status", "") else 0
            score += type_score
            score += 1 if getattr(template, "is_default_for_status", False) else 0
            candidates.append((score, getattr(template, "id", 0) or 0, template))
        if candidates:
            return sorted(candidates, key=lambda item: (item[0], item[1]), reverse=True)[0][2]
        return templates[0] if not event_type else None

    def mail_template_type_score(self, template, submission) -> int:
        return mail_template_type_score(template, submission)

    def dispatch(self, request: MailDispatchRequest, sender=None) -> bool:
        if not request.recipient or not request.subject or sender is None:
            return False
        try:
            sender(
                to=request.recipient,
                subject=request.subject,
                html_body=request.body,
                context=request.context,
                event_type=request.event_type,
            )
            return True
        except Exception:
            return False

    def dispatch_raw(
        self,
        *,
        event_type: str,
        recipient: str,
        subject: str,
        html_body: str,
        text_body: str = "",
        context: dict[str, Any] | None = None,
        sender=None,
        db=None,
        form=None,
        submission=None,
        template=None,
        footer=None,
        sent_by_id: int | None = None,
    ) -> MailDispatchResult:
        recipient = str(recipient or "").strip()
        subject = str(subject or "").strip()
        if not recipient:
            log = self.log_email(
                db,
                form=form,
                submission=submission,
                template=template,
                footer=footer,
                to_email=recipient,
                subject=subject,
                sent_by_id=sent_by_id,
                status="skipped",
                error_message="Brak odbiorcy.",
            )
            return MailDispatchResult("skipped", recipient, subject, "Brak odbiorcy.", log)
        if not subject:
            log = self.log_email(
                db,
                form=form,
                submission=submission,
                template=template,
                footer=footer,
                to_email=recipient,
                subject=subject,
                sent_by_id=sent_by_id,
                status="skipped",
                error_message="Brak tematu.",
            )
            return MailDispatchResult("skipped", recipient, subject, "Brak tematu.", log)
        sender = sender or self.smtp_sender or getattr(self.notification_service, "smtp_sender", None)
        if sender is None:
            log = self.log_email(
                db,
                form=form,
                submission=submission,
                template=template,
                footer=footer,
                to_email=recipient,
                subject=subject,
                sent_by_id=sent_by_id,
                status="skipped",
                error_message="Brak adaptera SMTP.",
            )
            return MailDispatchResult("skipped", recipient, subject, "Brak adaptera SMTP.", log)
        try:
            sender(
                smtp_host=current_app.config["SMTP_HOST"],
                smtp_port=current_app.config["SMTP_PORT"],
                smtp_user=current_app.config["SMTP_USER"],
                smtp_password=current_app.config["SMTP_PASSWORD"],
                mail_from=current_app.config["MAIL_FROM"],
                to_emails=[recipient],
                subject=subject,
                html_body=html_body,
                text_body=text_body or html_body,
                use_tls=current_app.config.get("SMTP_USE_TLS", True),
                use_ssl=current_app.config.get("SMTP_USE_SSL", False),
                timeout=current_app.config.get("SMTP_TIMEOUT", 30),
            )
            log = self.log_email(
                db,
                form=form,
                submission=submission,
                template=template,
                footer=footer,
                to_email=recipient,
                subject=subject,
                sent_by_id=sent_by_id,
                status="sent",
            )
            return MailDispatchResult("sent", recipient, subject, log=log)
        except Exception as exc:
            error_message = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            current_app.logger.exception("Nie udalo sie wyslac maila do %s: %s", recipient, error_message)
            log = self.log_email(
                db,
                form=form,
                submission=submission,
                template=template,
                footer=footer,
                to_email=recipient,
                subject=subject,
                sent_by_id=sent_by_id,
                status="failed",
                error_message=error_message,
            )
            return MailDispatchResult("failed", recipient, subject, error_message, log)

    def dispatch_to_submission(
        self,
        *,
        db,
        form,
        submission,
        template=None,
        footer=None,
        to_email: str = "",
        subject_template: str = "",
        event_type: str = "manual",
        sent_by_id: int | None = None,
        files: list | None = None,
        context_builders: dict[str, Any] | None = None,
        logo_url_builder=None,
    ) -> MailDispatchResult:
        context = self.build_context_for_submission(form, submission, files or [], **(context_builders or {}))
        subject = self.render_subject(subject_template or getattr(template, "subject", ""), context)
        footer_html = self.build_footer(footer, logo_url_builder=logo_url_builder)
        html_body = render_platform_mail_html(template, context, footer_html=footer_html)
        text_body = render_platform_mail_text(template, context)
        return self.dispatch_raw(
            event_type=event_type,
            recipient=to_email or getattr(submission, "email", ""),
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            context=context,
            db=db,
            form=form,
            submission=submission,
            template=template,
            footer=footer,
            sent_by_id=sent_by_id,
        )

    def dispatch_decision_email(self, submission_id: str, decision: str) -> MailDispatchResult:
        if not self.submission_repository:
            return MailDispatchResult("skipped", error_message="Brak repozytorium zgloszen.")
        submission = self.submission_repository.get_by_id(submission_id)
        if not submission:
            return MailDispatchResult("skipped", error_message="Brak zgloszenia.")
        decision_key = self._decision_key(decision)
        if self._decision_email_already_sent(submission, decision_key):
            return MailDispatchResult("skipped", submission.get("email", ""), error_message="Mail decyzji juz wyslany.")
        email = str(submission.get("email") or "").strip()
        if not email:
            return MailDispatchResult("skipped", error_message="Brak odbiorcy.")
        accepted = decision_key == "accepted"
        form_title = submission.get("form_name", "")
        subject = "Wniosek zaakceptowany - dokumenty do podpisu" if accepted else "Wniosek nie zostal zaakceptowany"
        context = {
            "submission_id": submission_id,
            "form_title": form_title,
            "submission": submission,
            "accepted": accepted,
            "decision": decision_key,
        }
        html_body = self._render_decision_html(context)
        text_body = (
            "Dzien dobry,\n\n"
            f"wniosek dotyczacy formularza \"{form_title}\" zostal zaakceptowany.\n\n"
            f"ID wniosku: {submission_id}\n\n"
            "Mozesz przejsc do podpisywania dokumentow w zakladce \"Do podpisania\".\n\n"
            "Pozdrawiamy\n"
            if accepted
            else
            "Dzien dobry,\n\n"
            f"wniosek dotyczacy formularza \"{form_title}\" nie zostal zaakceptowany.\n\n"
            f"ID wniosku: {submission_id}\n\n"
            "W razie pytan prosimy o kontakt z urzedem.\n\n"
            "Pozdrawiamy\n"
        )
        result = self.dispatch_raw(
            event_type="officer_decision",
            recipient=email,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            context=context,
        )
        if result.sent:
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
        return result

    def log_email(
        self,
        db,
        *,
        form=None,
        submission=None,
        template=None,
        footer=None,
        to_email: str = "",
        subject: str = "",
        sent_by_id: int | None = None,
        status: str = "sent",
        error_message: str = "",
    ):
        if db is None:
            return None
        try:
            from models import EmailLog

            log = EmailLog(
                form_id=getattr(form, "id", None),
                submission_id=getattr(submission, "id", None),
                public_submission_id=getattr(submission, "submission_id", "") or "",
                to_email=to_email or "",
                subject=subject or "",
                template_id=getattr(template, "id", None),
                footer_id=getattr(footer, "id", None),
                sent_by_id=sent_by_id,
                status=status,
                error_message=error_message or "",
            )
            db.add(log)
            return log
        except Exception:
            log = SimpleNamespace(
                form_id=getattr(form, "id", None),
                submission_id=getattr(submission, "id", None),
                public_submission_id=getattr(submission, "submission_id", "") or "",
                to_email=to_email or "",
                subject=subject or "",
                template_id=getattr(template, "id", None),
                footer_id=getattr(footer, "id", None),
                sent_by_id=sent_by_id,
                status=status,
                error_message=error_message or "",
            )
            try:
                db.add(log)
            except Exception:
                return None
            return log

    def _render_decision_html(self, context: dict[str, Any]) -> str:
        template_name = "emails/decision_accepted.html" if context.get("accepted") else "emails/decision_rejected.html"
        try:
            return render_template(template_name, **context)
        except TemplateNotFound:
            accepted = bool(context.get("accepted"))
            heading = "Wniosek zaakceptowany" if accepted else "Wniosek nie zostal zaakceptowany"
            details = "Mozesz przejsc do podpisywania dokumentow." if accepted else "W razie pytan prosimy o kontakt z urzedem."
            return (
                "<!doctype html><html lang=\"pl\"><body>"
                f"<h2>{heading}</h2>"
                "<p>Dzien dobry,</p>"
                f"<p>Wniosek dotyczacy formularza <strong>{escape(str(context.get('form_title', '')))}</strong> "
                f"{'zostal zaakceptowany przez urzednika.' if accepted else 'nie zostal zaakceptowany przez urzednika.'}</p>"
                f"<p><strong>ID wniosku:</strong> {escape(str(context.get('submission_id', '')))}</p>"
                f"<p>{details}</p><p>Pozdrawiamy</p>"
                "</body></html>"
            )

    def _decision_key(self, decision: str) -> str:
        normalized = str(decision or "").strip().lower()
        if normalized in {"accepted", "tak", "officer_accepted"}:
            return "accepted"
        if normalized in {"rejected", "nie", "officer_rejected"}:
            return "rejected"
        return normalized

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
