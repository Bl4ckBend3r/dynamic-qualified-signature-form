from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from flask import current_app, url_for

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
        mail_settings_service=None,
    ) -> None:
        self.notification_service = notification_service
        self.submission_repository = submission_repository
        self.audit_log_service = audit_log_service
        self.smtp_sender = smtp_sender
        self.mail_settings_service = mail_settings_service

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
        inline_images: list[dict[str, Any]] | None = None,
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
        smtp_config = None
        if self.mail_settings_service and db is not None and form is not None:
            smtp_config = self.mail_settings_service.resolve_smtp(db, form, current_app.config)
        if smtp_config is None and not self.mail_settings_service:
            smtp_config = {
                "smtp_host": current_app.config.get("SMTP_HOST", ""),
                "smtp_port": current_app.config.get("SMTP_PORT", 587),
                "smtp_user": current_app.config.get("SMTP_USER", ""),
                "smtp_password": current_app.config.get("SMTP_PASSWORD", ""),
                "mail_from": current_app.config.get("MAIL_FROM", ""),
                "sender_name": current_app.config.get("MAIL_SENDER_NAME", ""),
                "reply_to": current_app.config.get("MAIL_REPLY_TO", ""),
                "use_tls": current_app.config.get("SMTP_USE_TLS", True),
                "use_ssl": current_app.config.get("SMTP_USE_SSL", False),
                "timeout": current_app.config.get("SMTP_TIMEOUT", 30),
            }
        if smtp_config is None:
            log = self.log_email(
                db, form=form, submission=submission, template=template, footer=footer,
                to_email=recipient, subject=subject, sent_by_id=sent_by_id,
                status="skipped", error_message="Brak konfiguracji SMTP.",
            )
            current_app.logger.warning("mail_skipped reason=smtp_not_configured form_id=%s", getattr(form, "id", None))
            return MailDispatchResult("skipped", recipient, subject, "Brak konfiguracji SMTP.", log)
        try:
            sender(
                **smtp_config,
                to_emails=[recipient],
                subject=subject,
                html_body=html_body,
                text_body=text_body or html_body,
                inline_images=inline_images or [],
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
            current_app.logger.exception("mail_send_failed form_id=%s error=%s", getattr(form, "id", None), error_message)
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
        extra_context: dict[str, Any] | None = None,
        logo_url_builder=None,
    ) -> MailDispatchResult:
        if template is None or getattr(template, "is_active", True) is False:
            current_app.logger.warning("mail_skipped reason=template_missing_or_inactive event=%s", event_type)
            log = self.log_email(
                db,
                form=form,
                submission=submission,
                template=template,
                footer=footer,
                to_email=to_email or getattr(submission, "email", ""),
                sent_by_id=sent_by_id,
                status="skipped",
                error_message="Brak aktywnego szablonu maila.",
            )
            return MailDispatchResult(
                "skipped",
                recipient=to_email or getattr(submission, "email", ""),
                error_message="Brak aktywnego szablonu maila.",
                log=log,
            )
        context = self.build_context_for_submission(form, submission, files or [], **(context_builders or {}))
        context.update(extra_context or {})
        subject = self.render_subject(subject_template or getattr(template, "subject", ""), context)
        footer_html = self.build_footer(footer, logo_url_builder=logo_url_builder)
        layout = self._layout_for_db(db)
        html_body = render_platform_mail_html(template, context, footer_html=footer_html, layout=layout)
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
            inline_images=layout.get("_inline_images", []),
        )

    def dispatch_decision_email(self, submission_id: str, decision: str) -> MailDispatchResult:
        current_app.logger.info("mail_skipped reason=automatic_decision_email_disabled submission_id=%s", submission_id)
        return MailDispatchResult("skipped", error_message="Automatyczny mail decyzji jest wyłączony.")

    def dispatch_submission_received(self, submission_id: str) -> MailDispatchResult:
        database_url = str(current_app.config.get("DATABASE_URL") or "").strip()
        if not database_url:
            return MailDispatchResult("skipped", error_message="Brak bazy konfiguracji maili.")
        from database import create_session_factory
        from models import Form, FormSubmission, PlatformMailTemplate
        from sqlalchemy import select

        with create_session_factory(database_url)() as db:
            submission = db.execute(
                select(FormSubmission).where(FormSubmission.submission_id == submission_id)
            ).scalar_one_or_none()
            if submission is None:
                return MailDispatchResult("skipped", error_message="Brak zgłoszenia.")
            form = db.execute(select(Form).where(Form.slug == submission.form_slug)).scalar_one_or_none()
            template = db.execute(
                select(PlatformMailTemplate).where(PlatformMailTemplate.template_type == "submission_received")
            ).scalar_one_or_none()
            if form is None or template is None or not template.is_active:
                current_app.logger.warning("mail_skipped reason=submission_template_missing_or_inactive form_slug=%s", submission.form_slug)
                log = self.log_email(
                    db,
                    form=form,
                    submission=submission,
                    to_email=str(submission.email or ""),
                    status="skipped",
                    error_message="Brak aktywnego szablonu submission_received.",
                )
                db.commit()
                return MailDispatchResult(
                    "skipped",
                    recipient=str(submission.email or ""),
                    error_message="Brak aktywnego szablonu submission_received.",
                    log=log,
                )
            if not str(submission.email or "").strip():
                log = self.log_email(
                    db,
                    form=form,
                    submission=submission,
                    status="skipped",
                    error_message="Brak odbiorcy.",
                )
                db.commit()
                return MailDispatchResult("skipped", error_message="Brak odbiorcy.", log=log)
            context = self.build_context_for_submission(
                form,
                submission,
                [],
                status_url=url_for("documents.documents_to_sign", submission_id=submission.submission_id, _external=True),
                platform_url=url_for("public_forms.index", _external=True),
                submission_date=submission.created_at,
            )
            subject = self.render_subject(template.subject, context)
            layout = self._layout_for_db(db)
            html_body = render_platform_mail_html(template, context, layout=layout)
            text_body = render_platform_mail_text(template, context)
            result = self.dispatch_raw(
                event_type="submission_received",
                recipient=submission.email,
                subject=subject,
                html_body=html_body,
                text_body=text_body,
                context=context,
                db=db,
                form=form,
                submission=submission,
                template=None,
                inline_images=self._inline_images_from_layout(layout),
            )
            db.commit()
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

    def _layout_for_db(self, db) -> dict[str, Any]:
        if not self.mail_settings_service or db is None:
            return {}
        layout = self.mail_settings_service.get_layout(db)
        logo_id = layout.get("logo_id")
        if logo_id and layout.get("logo_position") != "none":
            from models import Logo

            logo = db.get(Logo, logo_id)
            if logo and logo.active:
                try:
                    logo_path = Path(str(logo.storage_path or ""))
                    content = logo_path.read_bytes() if logo_path.is_file() else b""
                    mime_type = str(logo.mime_type or "").strip().lower()
                    if content and mime_type.startswith("image/"):
                        content_id = f"platform-logo-{logo.id}"
                        layout["logo_url"] = f"cid:{content_id}"
                        layout["_inline_images"] = [{
                            "cid": content_id,
                            "content": content,
                            "mime_type": mime_type,
                            "filename": Path(str(logo.filename or "logo")).name,
                        }]
                    else:
                        current_app.logger.warning(
                            "mail_logo_skipped logo_id=%s reason=asset_unavailable_or_invalid", logo.id
                        )
                except (OSError, ValueError):
                    current_app.logger.warning("mail_logo_skipped logo_id=%s reason=asset_unavailable", logo.id)
        return layout

    @staticmethod
    def _inline_images_from_layout(layout: dict[str, Any]) -> list[dict[str, Any]]:
        images = layout.get("_inline_images")
        return images if isinstance(images, list) else []
