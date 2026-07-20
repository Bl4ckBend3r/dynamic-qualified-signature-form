from __future__ import annotations

from dataclasses import dataclass
from html import escape
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from flask import current_app, url_for

from services.admin_mail_context_service import build_mail_context, mail_template_type_score
from services.instruction_html_service import sanitize_instruction_html
from services.mail_footer_resolver import MailFooterResolver
from services.mail_template_service import render_platform_mail_html, render_platform_mail_text, render_template_text


logger = logging.getLogger(__name__)


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
        mail_footer_resolver=None,
    ) -> None:
        self.notification_service = notification_service
        self.submission_repository = submission_repository
        self.audit_log_service = audit_log_service
        self.smtp_sender = smtp_sender
        self.mail_settings_service = mail_settings_service
        self.mail_footer_resolver = mail_footer_resolver or MailFooterResolver()

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
        alignment = str(getattr(footer, "logo_alignment", "left") or "left")
        if alignment not in {"left", "center", "right"}:
            alignment = "left"
        position = str(getattr(footer, "logo_position", "top") or "top")
        if position not in {"top", "bottom", "left", "right", "inline"}:
            position = "top"
        width = self._footer_logo_dimension(getattr(footer, "logo_width", None), 20, 800)
        height = self._footer_logo_dimension(getattr(footer, "logo_height", None), 20, 400)

        logo = getattr(footer, "logo", None)
        logo_html = ""
        inline_logo_html = ""
        if logo and getattr(logo, "active", False) and logo_url_builder:
            logo_url = logo_url_builder(logo)
            image_width = width if width is not None else (None if height is not None else 160)
            image_styles = ["display:inline-block", "max-width:800px", "max-height:400px"]
            image_styles.append(f"width:{image_width}px" if image_width is not None else "width:auto")
            image_styles.append(f"height:{height}px" if height is not None else "height:auto")
            image_html = (
                f'<img src="{escape(str(logo_url))}" alt="{escape(str(getattr(logo, "name", "")))}" '
                f'style="{";".join(image_styles)};">'
            )
            logo_html = f'<div class="mail-footer-logo" style="text-align:{alignment};">{image_html}</div>'
            inline_logo_html = (
                f'<span class="mail-footer-logo" style="display:inline-block;text-align:{alignment};">'
                f"{image_html}</span>"
            )

        html_body = sanitize_instruction_html(getattr(footer, "html_body", "") or "")
        content_parts = []
        contact_html = sanitize_instruction_html(getattr(footer, "contact_html", "") or "")
        if contact_html:
            content_parts.append(f'<div class="mail-footer-contact">{contact_html}</div>')
        links = getattr(footer, "links", None) or []
        link_parts = []
        for item in links:
            if not isinstance(item, dict):
                continue
            label = escape(str(item.get("label") or ""))
            url = str(item.get("url") or "")
            if label and url.lower().startswith(("https://", "http://", "mailto:", "tel:")):
                link_parts.append(f'<a href="{escape(url, quote=True)}" rel="noopener noreferrer">{label}</a>')
        if link_parts:
            content_parts.append('<div class="mail-footer-links">' + " · ".join(link_parts) + "</div>")
        legal_text = sanitize_instruction_html(getattr(footer, "legal_text", "") or "")
        if legal_text:
            content_parts.append(f'<div class="mail-footer-legal">{legal_text}</div>')

        placeholder = "{{ footer_logo }}"
        if position == "inline":
            if placeholder in html_body:
                html_body = html_body.replace(placeholder, inline_logo_html)
                logo_html = ""
            elif logo_html:
                logger.warning(
                    "mail_footer_inline_logo_placeholder_missing footer_id=%s",
                    getattr(footer, "id", None),
                )
                position = "bottom"
        elif placeholder in html_body:
            html_body = html_body.replace(placeholder, "")
        if html_body:
            content_parts.insert(0, html_body)
        content_html = "\n".join(content_parts)

        if not logo_html:
            return content_html
        if position == "bottom":
            separator = "\n" if content_html else ""
            return content_html + separator + f'<div style="margin-top:16px;">{logo_html}</div>'
        if position in {"left", "right"}:
            padding = "0 16px 0 0" if position == "left" else "0 0 0 16px"
            logo_cell = f'<td style="vertical-align:top;padding:{padding};">{logo_html}</td>'
            content_cell = f'<td style="vertical-align:top;width:100%;">{content_html}</td>'
            cells = logo_cell + content_cell if position == "left" else content_cell + logo_cell
            return (
                '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
                f'style="width:100%;border-collapse:collapse;"><tr>{cells}</tr></table>'
            )
        separator = "\n" if content_html else ""
        return f'<div style="margin-bottom:16px;">{logo_html}</div>' + separator + content_html

    @staticmethod
    def _footer_logo_dimension(value, minimum: int, maximum: int) -> int | None:
        try:
            parsed = int(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None
        return parsed if parsed is not None and minimum <= parsed <= maximum else None

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
        footer = self.mail_footer_resolver.resolve(
            db,
            mail_type=event_type,
            form=form,
            submission=submission,
        )
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
        if footer:
            layout = {**layout, "footer_html": ""}
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
            footer = self.mail_footer_resolver.resolve(
                db,
                mail_type="submission_received",
                form=form,
                submission=submission,
            )
            footer_html = self.build_footer(
                footer,
                logo_url_builder=lambda logo: url_for(
                    "public_forms.logo_asset",
                    logo_id=logo.id,
                    filename=logo.filename,
                    _external=True,
                ),
            )
            if footer:
                layout = {**layout, "footer_html": ""}
            html_body = render_platform_mail_html(template, context, footer_html=footer_html, layout=layout)
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
                footer=footer,
                inline_images=self._inline_images_from_layout(layout),
            )
            db.commit()
            return result

    def dispatch_auto_rejected_by_condition(self, submission_id: str, evaluation: dict) -> MailDispatchResult:
        message = str(evaluation.get("user_message") or "").strip() or (
            "Zgłoszenie zostało zapisane, ale nie spełnia warunków udziału."
        )
        return self._dispatch_special_submission_event(
            submission_id,
            event_type="auto_rejected_by_condition",
            default_name="Automatyczne odrzucenie zgłoszenia",
            default_subject="Zgłoszenie {{ submission_id }} nie spełnia warunków udziału",
            default_html=(
                "<p>Zgłoszenie zostało zapisane, ale nie spełnia warunków udziału.</p>"
                "<p>{{ qualification_message_html }}</p>"
            ),
            default_text=(
                "Zgłoszenie zostało zapisane, ale nie spełnia warunków udziału.\n"
                "{{ qualification_message }}"
            ),
            extra_context={
                "qualification_message": message,
                "qualification_message_html": escape(message),
            },
        )

    def dispatch_returned_for_correction(
        self,
        submission_id: str,
        *,
        reason: str,
        message_to_user: str,
        cleared: bool,
        recipient: str = "",
    ) -> MailDispatchResult:
        return self._dispatch_special_submission_event(
            submission_id,
            event_type="returned_for_correction",
            default_name="Zgłoszenie wysłane do poprawy",
            default_subject="Zgłoszenie {{ submission_id }} wymaga poprawy",
            default_html=(
                "<p>Zgłoszenie wymaga ponownego uzupełnienia.</p>"
                "<p><strong>Powód:</strong> {{ correction_reason_html }}</p>"
                "<p>{{ correction_message_html }}</p>"
                "<p>{{ correction_clear_info_html }}</p>"
                '<p><a href="{{ correction_url }}">Uzupełnij formularz ponownie</a></p>'
            ),
            default_text=(
                "Zgłoszenie wymaga ponownego uzupełnienia.\n"
                "Powód: {{ correction_reason }}\n{{ correction_message }}\n"
                "{{ correction_clear_info }}\n{{ correction_url }}"
            ),
            extra_context={
                "correction_reason": reason,
                "correction_reason_html": escape(reason),
                "correction_message": message_to_user,
                "correction_message_html": escape(message_to_user),
                "correction_clear_info": (
                    "Poprzednie dane zostały wyczyszczone."
                    if cleared
                    else "Poprzednie dane pozostawiono do ponownej edycji."
                ),
                "correction_clear_info_html": escape(
                    "Poprzednie dane zostały wyczyszczone."
                    if cleared
                    else "Poprzednie dane pozostawiono do ponownej edycji."
                ),
            },
            recipient=recipient,
            include_correction_url=True,
        )

    def _dispatch_special_submission_event(
        self,
        submission_id: str,
        *,
        event_type: str,
        default_name: str,
        default_subject: str,
        default_html: str,
        default_text: str,
        extra_context: dict,
        recipient: str = "",
        include_correction_url: bool = False,
    ) -> MailDispatchResult:
        database_url = str(current_app.config.get("DATABASE_URL") or "").strip()
        if not database_url:
            return MailDispatchResult("skipped", error_message="Brak bazy konfiguracji maili.")
        from database import create_session_factory
        from models import Form, FormSubmission, MailTemplate, PlatformMailTemplate
        from sqlalchemy import select

        with create_session_factory(database_url)() as db:
            submission = db.execute(
                select(FormSubmission).where(FormSubmission.submission_id == submission_id)
            ).scalar_one_or_none()
            if submission is None:
                return MailDispatchResult("skipped", error_message="Brak zgłoszenia.")
            form = db.execute(select(Form).where(Form.slug == submission.form_slug)).scalar_one_or_none()
            if form is None:
                return MailDispatchResult("skipped", error_message="Brak formularza.")
            template = db.execute(
                select(MailTemplate)
                .where(
                    MailTemplate.form_id == form.id,
                    MailTemplate.template_type == event_type,
                    MailTemplate.is_active.is_(True),
                )
                .order_by(MailTemplate.id.desc())
            ).scalars().first()
            if template is None:
                template = db.execute(
                    select(PlatformMailTemplate).where(
                        PlatformMailTemplate.template_type == event_type,
                        PlatformMailTemplate.is_active.is_(True),
                    )
                ).scalar_one_or_none()
            if template is None:
                template = SimpleNamespace(
                    name=default_name,
                    subject=default_subject,
                    html_body=default_html,
                    text_body=default_text,
                    is_active=True,
                )
            context = dict(extra_context)
            if include_correction_url:
                context["correction_url"] = url_for(
                    "public_forms.correct_submission",
                    slug=submission.form_slug,
                    submission_id=submission.submission_id,
                    token=submission.access_token,
                    _external=True,
                )
            result = self.dispatch_to_submission(
                db=db,
                form=form,
                submission=submission,
                template=template,
                to_email=recipient or submission.email,
                subject_template=getattr(template, "subject", ""),
                event_type=event_type,
                files=self.submission_repository.list_submission_files(submission_id),
                extra_context=context,
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
