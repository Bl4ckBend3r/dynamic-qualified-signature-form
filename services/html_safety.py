from __future__ import annotations

from markupsafe import Markup

from services.mail_template_service import sanitize_content_html


HTML_ALLOWED_FIELDS = {
    "field.label",
    "field.description",
    "field.help_text",
    "field.option.label",
    "section.label",
    "static_text.label",
    "document.template_html",
    "mail.content_html",
    "mail.footer_html",
}


def sanitize_trusted_html(value: object) -> Markup:
    return Markup(sanitize_content_html(str(value or "")))
