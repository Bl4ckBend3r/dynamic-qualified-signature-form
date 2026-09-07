from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from html import escape
import logging
import mimetypes
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from flask import current_app, url_for
from sqlalchemy.exc import SQLAlchemyError

from services.admin_mail_context_service import build_mail_context, mail_template_type_score
from services.footer_logo_service import (
    normalize_footer_logo_alignment,
    normalize_footer_logo_dimension,
    normalize_footer_logo_position,
)
from services.instruction_html_service import sanitize_instruction_html
from services.mail_footer_resolver import MailFooterResolver
from services.mail_recipient_service import MailRecipientResolver
from services.mail_template_service import render_platform_mail_html, render_platform_mail_text, render_template_text
from services.training_availability_service import TrainingAvailabilityService


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
    deliveries: tuple = ()

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

    def dispatch_workflow_decision_event(self, *, db, form, submission, decision, actor_id=None, group_config=None, step_config=None):
        """Consume a configured decision event; all automatic delivery stays here."""
        from sqlalchemy import select
        from models import EmailLog, SubmissionDecision

        definition = (submission.form_version.definition_json if submission.form_version else {}) or {}
        workflow = definition.get("workflow") or {}
        item_event = decision is not None
        if workflow.get("flow_mode") == "explicit":
            step = step_config or next((s for s in workflow.get("steps", []) if s.get("id") == decision.workflow_step), {})
            config = step.get("decision_email") or {}
            enabled = config.get("enabled", False)
        else:
            config = (group_config or {}).get("decision_email") or {}
            enabled = config.get("automatic", False)
        if not enabled:
            return None
        template_type = str(config.get("template_type") or "repeatable_item_decision")
        template = self.select_template(list(form.mail_templates), submission, template_type)
        participant = dict(decision.participant_snapshot_json or {}) if item_event else {}
        recipient = str(participant.get("email") or "") if item_event else str(submission.email or "")
        if item_event:
            already_sent = db.execute(select(EmailLog.id).where(
                EmailLog.item_decision_id == decision.id, EmailLog.to_email == recipient,
                EmailLog.template_id == getattr(template, "id", None), EmailLog.status == "sent",
                EmailLog.administrator_message.is_(None),
            )).scalars().first()
            if already_sent:
                return None
        context = {"participant": participant, "submission_id": submission.submission_id,
                   "form_name": submission.form_name,
                   "workflow_step": decision.workflow_step if item_event else (step_config or {}).get("id"),
                   "decision_code": decision.decision_code if item_event else submission.officer_decision,
                   "decision_label": decision.decision_label if item_event else submission.officer_decision,
                   "decision_comment": decision.comment if item_event else submission.officer_decision_reason}
        if item_event:
            context["decision_date"] = decision.decided_at
        else:
            decision_id = db.execute(select(SubmissionDecision.id).where(SubmissionDecision.submission_id == submission.id)
                .order_by(SubmissionDecision.id.desc())).scalars().first()
        result = self.dispatch_to_submission(db=db, form=form, submission=submission, template=template,
            to_email=recipient, event_type=template_type, sent_by_id=actor_id, extra_context=context,
            recipient_source=config.get("recipient_source"),
            event_id=f"item_decision:{decision.id}" if item_event else f"submission_decision:{decision_id}" if decision_id else "",
            record_uuid=decision.item_id if item_event else "", group_key=decision.group_key if item_event else "",
            participant_snapshot=participant if item_event else None, item_decision_id=decision.id if item_event else None)
        if item_event and result.log:
            result.log.repeatable_group_key = decision.group_key
            result.log.repeatable_item_id = decision.item_id
            result.log.item_decision_id = decision.id
        return result

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
        builders.setdefault(
            "training_availability_service",
            TrainingAvailabilityService(self.submission_repository),
        )
        return build_mail_context(form, submission, files or [], **builders)

    def build_footer(self, footer=None, logo_url_builder=None, *, logo_url: str | None = None) -> str:
        if not footer:
            return ""
        alignment = normalize_footer_logo_alignment(getattr(footer, "logo_alignment", "left"))
        position = normalize_footer_logo_position(getattr(footer, "logo_position", "top"))
        width = normalize_footer_logo_dimension(getattr(footer, "logo_width", None), 20, 800)
        height = normalize_footer_logo_dimension(getattr(footer, "logo_height", None), 20, 400)

        logo = getattr(footer, "logo", None)
        logo_html = ""
        inline_logo_html = ""
        resolved_logo_url = logo_url if logo_url is not None else self._footer_logo_url(footer, logo_url_builder)
        if resolved_logo_url:
            image_width = width if width is not None else (None if height is not None else 160)
            image_styles = ["display:inline-block", "max-width:800px", "max-height:400px"]
            image_styles.append(f"width:{image_width}px" if image_width is not None else "width:auto")
            image_styles.append(f"height:{height}px" if height is not None else "height:auto")
            image_html = (
                f'<img src="{escape(resolved_logo_url, quote=True)}" '
                f'alt="{escape(str(getattr(logo, "name", "") or "Logo stopki"), quote=True)}" '
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
    def _footer_logo_url(footer, logo_url_builder=None) -> str:
        """Resolve a library logo for preview; mail delivery supplies a CID explicitly."""
        logo = getattr(footer, "logo", None)
        if logo_url_builder is None or logo is None or getattr(logo, "active", False) is False:
            return ""
        try:
            resolved = str(logo_url_builder(logo) or "").strip()
        except (TypeError, ValueError):
            return ""
        return resolved if resolved.lower().startswith(("https://", "http://", "cid:")) else ""

    @staticmethod
    def _footer_logo_dimension(value, minimum: int, maximum: int) -> int | None:
        return normalize_footer_logo_dimension(value, minimum, maximum)

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
        attachments: list | None = None,
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
                event_type=event_type,
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
                event_type=event_type,
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
                event_type=event_type,
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
                "timeout": current_app.config.get("SMTP_TIMEOUT", 10),
            }
        if smtp_config is None:
            log = self.log_email(
                db, form=form, submission=submission, template=template, footer=footer,
                to_email=recipient, subject=subject, sent_by_id=sent_by_id,
                status="skipped", event_type=event_type, error_message="Brak konfiguracji SMTP.",
            )
            current_app.logger.warning("mail_skipped reason=smtp_not_configured form_id=%s", getattr(form, "id", None))
            return MailDispatchResult("skipped", recipient, subject, "Brak konfiguracji SMTP.", log)
        log = self.log_email(
            db, form=form, submission=submission, template=template, footer=footer,
            to_email=recipient, subject=subject, sent_by_id=sent_by_id, status="pending",
            event_type=event_type, html_body=html_body, text_body=text_body,
        )
        if log is not None and callable(getattr(db, 'flush', None)):
            db.flush()
        try:
            sender(
                **smtp_config,
                to_emails=[recipient],
                subject=subject,
                html_body=html_body,
                text_body=text_body or html_body,
                inline_images=inline_images or [],
                attachments=attachments or [],
            )
            if log is not None:
                log.status = "sent"
            return MailDispatchResult("sent", recipient, subject, log=log)
        except Exception as exc:
            error_message = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            credential = getattr(submission, 'access_token', None)
            if credential:
                error_message = error_message.replace(credential, '[redacted]')
            metrics = current_app.extensions.get("observability_metrics")
            if metrics is not None:
                metrics.operation_failures.labels(operation="mail_dispatch", kind="smtp").inc()
            current_app.logger.error(
                "mail_dispatch_failed error=%s", type(exc).__name__,
                extra={"event": "mail_dispatch_failed", "operation": "mail_dispatch", "form_id": getattr(form, "id", None)},
            )
            if log is not None:
                log.status = "failed"
                log.error_message = error_message
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
        attachments: list | None = None,
        recipient_source: dict | None = None,
        event_id: str = "",
        record_uuid: str = "",
        group_key: str = "",
        participant_snapshot: dict | None = None,
        item_decision_id: int | None = None,
        force_send: bool = False,
    ) -> MailDispatchResult:
        if bool(record_uuid) != bool(group_key) or (participant_snapshot is not None and not record_uuid):
            return MailDispatchResult('failed', error_message='Zdarzenie osoby wymaga grupy i UUID odbiorcy.')
        version = getattr(submission, "form_version", None)
        definition = (version.definition_json if version else getattr(form, "definition_json", {})) or {}
        sources = [recipient_source] if recipient_source is not None else MailRecipientResolver.configured_sources(
            definition, event_type, template, step_id=str((extra_context or {}).get("workflow_step") or getattr(submission, "workflow_stage", "") or ""))
        if record_uuid and sources is None:
            sources = [{"type": "repeatable_group", "group": group_key}]
        if sources is not None:
            return self._dispatch_record_notifications(db=db, form=form, submission=submission, template=template,
                definition=definition, sources=sources, event_type=event_type, event_id=event_id,
                record_uuid=record_uuid, group_key=group_key, participant_snapshot=participant_snapshot,
                item_decision_id=item_decision_id, sent_by_id=sent_by_id, extra_context=extra_context,
                subject_template=subject_template, force_send=force_send)
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
                event_type=event_type,
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
        if getattr(template, "show_process_status", True) is False:
            context.update(
                process_status="",
                process_status_label="",
                current_stage="",
                current_stage_label="",
                status_label="",
            )
        subject = self.render_subject(subject_template or getattr(template, "subject", ""), context)
        footer_logo_url, footer_inline_images = self.footer_logo_for_email(db, footer)
        footer_html = self.build_footer(footer, logo_url=footer_logo_url)
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
            inline_images=self._inline_images_from_layout(layout) + footer_inline_images,
            attachments=attachments or [],
        )

    def dispatch_decision_email(self, submission_id: str, decision: str) -> MailDispatchResult:
        current_app.logger.info("mail_skipped reason=automatic_decision_email_disabled submission_id=%s", submission_id)
        return MailDispatchResult("skipped", error_message="Automatyczny mail decyzji jest wyłączony.")

    def dispatch_correction_accepted(self, submission_id: str) -> MailDispatchResult:
        """Send the configured, idempotent acceptance notification after correction."""
        database_url = str(current_app.config.get("DATABASE_URL") or "").strip()
        if not database_url:
            return MailDispatchResult("skipped", error_message="Brak bazy konfiguracji maili.")
        from database import create_session_factory
        from models import EmailLog, Form, FormSubmission, MailTemplate
        from sqlalchemy import or_, select

        with create_session_factory(database_url)() as db:
            submission = db.execute(
                select(FormSubmission).where(FormSubmission.submission_id == submission_id)
            ).scalar_one_or_none()
            if submission is None or not getattr(submission, "correction_completed_at", None):
                return MailDispatchResult("skipped", error_message="Zgłoszenie nie jest zaakceptowaną korektą.")
            form = db.execute(select(Form).where(Form.slug == submission.form_slug)).scalar_one_or_none()
            version = submission.form_version if getattr(submission, "form_version_id", None) else None
            form_definition = version.definition_json if version else getattr(form, "definition_json", {})
            workflow = ((form_definition or {}).get("workflow") or {}) if form else {}
            if form is None or not workflow.get("send_email_notifications"):
                return MailDispatchResult("skipped", error_message="Powiadomienia formularza są wyłączone.")
            duplicate = db.execute(
                select(EmailLog.id).where(
                    EmailLog.submission_id == submission.id,
                    EmailLog.event_type == "correction_accepted",
                    EmailLog.status.in_(("sent", "queued")),
                )
            ).first()
            template = db.execute(
                select(MailTemplate)
                .where(
                    MailTemplate.form_id == form.id,
                    MailTemplate.is_active.is_(True),
                    or_(MailTemplate.template_type == "correction_accepted", MailTemplate.trigger_event == "correction_accepted"),
                )
                .order_by(MailTemplate.id.desc())
            ).scalars().first()
            if template is None:
                return MailDispatchResult("skipped", error_message="Brak aktywnego szablonu correction_accepted.")
            sources = MailRecipientResolver.configured_sources(form_definition or {}, 'correction_accepted', template,
                step_id=submission.workflow_stage or submission.workflow_step)
            if sources is None:
                if not str(submission.email or '').strip():
                    return MailDispatchResult('skipped', error_message='Brak odbiorcy.')
                if duplicate:
                    return MailDispatchResult('skipped', error_message='Wiadomość dla tego zdarzenia została już wysłana.')
            result = self.dispatch_to_submission(
                db=db,
                form=form,
                submission=submission,
                template=template,
                event_type="correction_accepted",
                event_id=f"correction:{submission.correction_completed_at.isoformat()}",
                files=self.submission_repository.list_submission_files(submission_id),
            )
            self._commit_email_log(db)
            return result

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
                self._commit_email_log(db)
                return MailDispatchResult(
                    "skipped",
                    recipient=str(submission.email or ""),
                    error_message="Brak aktywnego szablonu submission_received.",
                    log=log,
                )
            definition = (submission.form_version.definition_json if submission.form_version else form.definition_json) or {}
            for event in ('submission_created', 'submission_received'):
                sources = MailRecipientResolver.configured_sources(definition, event, template, step_id=submission.workflow_stage or submission.workflow_step)
                if sources is not None:
                    return self._dispatch_record_notifications(db=db, form=form, submission=submission, template=template,
                        definition=definition, sources=sources, event_type=event, event_id='submission_created')
            if any(isinstance(field, dict) and field.get("type") == "repeatable_group"
                   and (field.get("submission_confirmation") or {}).get("enabled")
                   for field in definition.get("fields") or []):
                return self._dispatch_repeatable_confirmations(db, form, submission, template, definition)
            if not str(submission.email or "").strip():
                log = self.log_email(
                    db,
                    form=form,
                    submission=submission,
                    status="skipped",
                    error_message="Brak odbiorcy.",
                )
                self._commit_email_log(db)
                return MailDispatchResult("skipped", error_message="Brak odbiorcy.", log=log)
            context = self.build_context_for_submission(
                form,
                submission,
                [],
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
            footer_logo_url, footer_inline_images = self.footer_logo_for_email(db, footer)
            footer_html = self.build_footer(footer, logo_url=footer_logo_url)
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
                inline_images=self._inline_images_from_layout(layout) + footer_inline_images,
            )
            self._commit_email_log(db)
            return result

    def dispatch_form_draft_resume(self, draft_public_id: str, resume_url: str) -> MailDispatchResult:
        """Send a resumable-draft link without persisting its bearer token in EmailLog."""
        database_url = str(current_app.config.get("DATABASE_URL") or "").strip()
        if not database_url:
            return MailDispatchResult("skipped", error_message="Brak bazy konfiguracji maili.")
        from database import create_session_factory
        from models import Form, FormDraft, MailTemplate
        from sqlalchemy import or_, select

        with create_session_factory(database_url)() as db:
            draft = db.execute(select(FormDraft).where(FormDraft.public_id == draft_public_id)).scalar_one_or_none()
            if draft is None or draft.status != "active":
                return MailDispatchResult("skipped", error_message="Brak aktywnej wersji roboczej.")
            form = db.get(Form, draft.form_id)
            if form is None:
                return MailDispatchResult("skipped", error_message="Brak formularza.")
            template = db.execute(
                select(MailTemplate)
                .where(
                    MailTemplate.form_id == form.id,
                    MailTemplate.is_active.is_(True),
                    or_(MailTemplate.template_type == "form_draft_resume", MailTemplate.trigger_event == "form_draft_resume"),
                )
                .order_by(MailTemplate.id.desc())
            ).scalars().first()
            if template is None:
                template = SimpleNamespace(
                    name="Link do wersji roboczej formularza",
                    subject="Dokończ formularz {{ form_title }}",
                    html_body='<p>Twoja wersja robocza została zapisana.</p><p><a href="{{ draft_resume_url }}">Dokończ formularz</a></p><p>Link wygasa: {{ draft_expires_at }}</p>',
                    text_body="Twoja wersja robocza została zapisana.\n{{ draft_resume_url }}\nLink wygasa: {{ draft_expires_at }}",
                    is_active=True,
                    show_process_status=False,
                )
            result = self.dispatch_to_submission(
                db=db,
                form=form,
                submission=None,
                template=template,
                to_email=draft.email,
                event_type="form_draft_resume",
                extra_context={
                    "draft_resume_url": resume_url,
                    "draft_expires_at": draft.expires_at.isoformat(),
                    "form_title": form.title or form.name,
                },
            )
            if result.log is not None:
                result.log.html_body = "[redacted: resumable draft link]"
                result.log.text_body = "[redacted: resumable draft link]"
            self._commit_email_log(db)
            return result

    def _dispatch_repeatable_confirmations(self, db, form, submission, template, definition) -> MailDispatchResult:
        sources = [{"type": "repeatable_group", "group": field["name"],
                    "email_field": field["submission_confirmation"].get("email_field")}
                   for field in definition.get("fields", []) if field.get("type") == "repeatable_group"
                   and (field.get("submission_confirmation") or {}).get("enabled")]
        return self._dispatch_record_notifications(db=db, form=form, submission=submission, template=template,
            definition=definition, sources=sources, event_type="submission_created", event_id="submission_created",
            legacy_confirmation=True)

    def _record_mail_context(self, recipient, submission, form, definition, extra_context):
        from services.public_submission_status_service import build_readonly_submission_status
        person = dict(recipient.participant)
        status = build_readonly_submission_status({
            'process_status': submission.process_status, 'workflow_stage': submission.workflow_stage,
            'workflow_step': submission.workflow_step, 'document_states': submission.document_states,
        }, form_config=definition)['status_label']
        context = self.build_context_for_submission(form, submission, participant=person,
            can_access_submission=recipient.can_access_submission, definition=definition)
        context.update({
            'form_title': form.title or form.name,
            'current_status': status, 'status_label': status, 'process_status_label': status,
            'platform_url': url_for('public_forms.index', _external=True),
        })
        # Event metadata is explicit. Never merge the submission or arbitrary extra_context.
        allowed = {'workflow_step', 'decision_code', 'decision_label', 'decision_comment', 'decision_date',
                   'document_id', 'document_label', 'document_type', 'action_label', 'step_label',
                   'due_at', 'overdue_by', 'overdue_hours', 'trigger_event'}
        context.update({key: value for key, value in (extra_context or {}).items() if key in allowed and not isinstance(value, (dict, list, tuple))})
        if not recipient.can_access_submission and submission.access_token:
            context = {key: value.replace(submission.access_token, '[redacted]') if isinstance(value, str) else value for key, value in context.items()}
            context['participant'] = {key: value.replace(submission.access_token, '[redacted]') for key, value in person.items()}
        return context

    def _dispatch_record_notifications(self, *, db, form, submission, template, definition, sources,
            event_type, event_id='', record_uuid='', group_key='', participant_snapshot=None,
            item_decision_id=None, sent_by_id=None, extra_context=None, subject_template='',
            force_send=False, legacy_confirmation=False):
        from sqlalchemy import or_, select
        from models import EmailLog, FormSubmission, MailTemplate
        from form_loader import EMAIL_REGEX

        if template is None or getattr(template, 'is_active', True) is False:
            return MailDispatchResult('skipped', error_message='Brak aktywnego szablonu maila.')
        if bool(record_uuid) != bool(group_key):
            return MailDispatchResult('failed', error_message='Zdarzenie osoby wymaga grupy i UUID.')
        try:
            recipients = MailRecipientResolver().resolve(submission, definition, sources,
                people_service=current_app.extensions['services'].decision_definition_service,
                record_uuid=record_uuid, group_key=group_key, participant_snapshot=participant_snapshot)
        except ValueError as exc:
            return MailDispatchResult('failed', error_message=str(exc))
        template_kind = 'form' if isinstance(template, MailTemplate) else 'platform'
        template_content = {field: getattr(template, field, '') for field in (
            'name', 'subject', 'html_body', 'text_body', 'body_html', 'body_text', 'content_html', 'content_text', 'content_title',
            'content_intro', 'instruction_html', 'instruction_text', 'footer_note', 'show_process_status', 'use_platform_layout', 'mail_type')}
        frozen_template = SimpleNamespace(**template_content)
        template_revision = sha256(json.dumps(template_content, sort_keys=True, default=str).encode()).hexdigest()
        template_key = f'{template_kind}:{getattr(template, "id", "fallback")}:{template_revision}'
        occurrence = str(event_id or f'{submission.workflow_stage or submission.workflow_step}:{submission.process_status}')
        submission_pk = submission.id
        results = []
        # Persist the domain event before SMTP; each attempt then commits independently.
        # SMTP acceptance followed by a process crash before commit remains inherently ambiguous.
        db.commit()
        for recipient in recipients:
            if db.get_bind().dialect.name == 'sqlite':
                db.connection().exec_driver_sql('BEGIN IMMEDIATE')
            db.execute(select(FormSubmission.id).where(FormSubmission.id == submission_pk).with_for_update()).scalar_one()
            identity = [submission_pk, event_type, occurrence, recipient.group_key, recipient.record_uuid,
                        recipient.email, template_key, subject_template]
            marker = json.dumps({'idempotency_key': sha256(json.dumps(identity).encode()).hexdigest(),
                'event_id': occurrence, 'template': template_key}, sort_keys=True)
            matches = [EmailLog.administrator_message == marker]
            if legacy_confirmation:
                matches.append(EmailLog.administrator_message == f'confirmation_template:platform:{template.id}')
            already_sent = db.execute(select(EmailLog.id).where(
                EmailLog.submission_id == submission_pk, EmailLog.event_type == event_type,
                EmailLog.repeatable_group_key == recipient.group_key, EmailLog.repeatable_item_id == recipient.record_uuid,
                EmailLog.to_email == recipient.email, EmailLog.status == 'sent', or_(*matches),
            )).first()
            if already_sent and not force_send:
                db.commit()
                continue
            log_template = template if isinstance(template, MailTemplate) else None
            try:
                if not EMAIL_REGEX.fullmatch(recipient.email):
                    raise ValueError('Niepoprawny adres e-mail odbiorcy.')
                context = self._record_mail_context(recipient, submission, form, definition, extra_context)
                if getattr(frozen_template, 'show_process_status', True) is False:
                    context.update(current_status='', status_label='', process_status_label='')
                footer = self.mail_footer_resolver.resolve(db, mail_type=event_type, form=form, submission=submission)
                logo_url, footer_images = self.footer_logo_for_email(db, footer)
                layout = self._layout_for_db(db)
                if footer:
                    layout = {**layout, 'footer_html': ''}
                result = self.dispatch_raw(event_type=event_type, recipient=recipient.email,
                    subject=self.render_subject(subject_template or frozen_template.subject, context),
                    html_body=render_platform_mail_html(frozen_template, context, footer_html=self.build_footer(footer, logo_url=logo_url), layout=layout),
                    text_body=render_platform_mail_text(frozen_template, context), context=context, db=db, form=form,
                    submission=submission, template=log_template, footer=footer, sent_by_id=sent_by_id,
                    inline_images=self._inline_images_from_layout(layout) + footer_images)
            except Exception as exc:
                log = self.log_email(db, form=form, submission=submission, template=log_template, to_email=recipient.email,
                    sent_by_id=sent_by_id, event_type=event_type, status='failed', error_message=type(exc).__name__)
                result = MailDispatchResult('failed', recipient.email, error_message=type(exc).__name__, log=log)
            if result.log is not None:
                result.log.repeatable_group_key = recipient.group_key
                result.log.repeatable_item_id = recipient.record_uuid
                result.log.item_decision_id = item_decision_id
                result.log.administrator_message = marker
                if submission.access_token:
                    for field in ('html_body', 'text_body', 'subject', 'error_message'):
                        setattr(result.log, field, str(getattr(result.log, field) or '').replace(submission.access_token, '[redacted]'))
            db.commit()
            results.append(result)
        status = 'failed' if any(result.status == 'failed' for result in results) else 'sent' if any(result.sent for result in results) else 'skipped'
        only = results[0] if len(results) == 1 else None
        return MailDispatchResult(status, recipient=only.recipient if only else '', subject=only.subject if only else '',
            error_message=only.error_message if only else '', log=only.log if only else None, deliveries=tuple(results))

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
            self._commit_email_log(db)
            return result

    def _commit_email_log(self, db) -> bool:
        try:
            db.commit()
            return True
        except SQLAlchemyError:
            db.rollback()
            current_app.logger.exception(
                "Nie udało się zapisać email_logs. "
                "Sprawdź migracje bazy danych."
            )
            return False

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
        event_type: str = "email_delivery",
        error_message: str = "",
        html_body: str = "",
        text_body: str = "",
    ):
        if db is None:
            return None
        credential = getattr(submission, 'access_token', None)
        if credential:
            subject, html_body, text_body, error_message = (
                str(value or '').replace(credential, '[redacted]')
                for value in (subject, html_body, text_body, error_message)
            )
        try:
            from models import EmailLog

            log = EmailLog(
                form_id=getattr(form, "id", None),
                submission_id=getattr(submission, "id", None),
                public_submission_id=getattr(submission, "submission_id", "") or "",
                to_email=to_email or "",
                subject=subject or "",
                html_body=html_body or "",
                text_body=text_body or "",
                template_id=getattr(template, "id", None),
                footer_id=getattr(footer, "id", None),
                sent_by_id=sent_by_id,
                status=status,
                event_type=event_type or "email_delivery",
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
                html_body=html_body or "",
                text_body=text_body or "",
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

    def footer_logo_for_email(self, db, footer) -> tuple[str, list[dict[str, Any]]]:
        """Resolve a footer logo to an inline CID attachment, never an external/local img URL."""
        if footer is None:
            return "", []

        logo_id = getattr(footer, "logo_id", None)
        logo_path = str(getattr(footer, "logo_path", "") or "").strip()
        logo = None
        asset_path = None
        mime_type = ""
        filename = "footer-logo"

        if logo_id is not None and db is not None:
            from models import Logo

            logo = db.get(Logo, logo_id)
            if logo and getattr(logo, "active", False):
                asset_path = self._existing_footer_logo_path(getattr(logo, "storage_path", ""))
                mime_type = str(getattr(logo, "mime_type", "") or "").strip().lower()
                filename = Path(str(getattr(logo, "filename", "") or filename)).name
        elif logo_path:
            asset_path = self._existing_footer_logo_path(logo_path)
            filename = Path(logo_path).name or filename

        if asset_path and not mime_type.startswith("image/"):
            mime_type = str(mimetypes.guess_type(filename)[0] or "").lower()

        try:
            content = asset_path.read_bytes() if asset_path and mime_type.startswith("image/") else b""
        except OSError:
            content = b""

        if not content:
            if logo_id is not None or logo_path:
                current_app.logger.warning(
                    "Nie udało się załadować logo stopki mailowej: logo_id=%s, logo_path=%s",
                    logo_id,
                    logo_path,
                )
            return "", []

        return "cid:footer-logo", [{
            "cid": "footer-logo",
            "content": content,
            "mime_type": mime_type,
            "filename": filename,
        }]

    @staticmethod
    def _existing_footer_logo_path(value: object) -> Path | None:
        raw_value = str(value or "").strip()
        if not raw_value or raw_value.lower().startswith(("http://", "https://", "cid:", "data:")):
            return None
        raw_path = Path(raw_value)
        candidates = [raw_path]
        if not raw_path.is_absolute():
            candidates.extend([
                Path(current_app.root_path) / raw_path,
                Path(current_app.config.get("TEMP_DIR", current_app.root_path)) / raw_path,
            ])
        for candidate in candidates:
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
        return None

    @staticmethod
    def _inline_images_from_layout(layout: dict[str, Any]) -> list[dict[str, Any]]:
        images = layout.get("_inline_images")
        return images if isinstance(images, list) else []
