from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any

from services.mail_template_service import render_template_text
from services.admin_mail_context_service import build_mail_context


@dataclass(frozen=True)
class MailDispatchRequest:
    event_type: str
    recipient: str
    subject: str
    body: str
    context: dict[str, Any]


class MailDispatchService:
    """Future home for centralized mail dispatch decisions."""

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

    def select_template(self, templates: list[Any], event_type: str | None = None):
        if not templates:
            return None
        if not event_type:
            return templates[0]
        for template in templates:
            if getattr(template, "trigger_event", None) == event_type:
                return template
        return templates[0]

    def dispatch(self, request: MailDispatchRequest, sender=None) -> bool:
        if not request.recipient or not request.subject:
            return False
        if sender is None:
            # TODO(P1.3): wire NotificationService/admin manual e-mail through this facade.
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
