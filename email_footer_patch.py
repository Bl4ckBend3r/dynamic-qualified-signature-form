from __future__ import annotations

import html
import logging
import mimetypes
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from services import email_service

logger = logging.getLogger(__name__)
FOOTER_CID = "email-footer-logo"
_CURRENT_FORM_TITLE = ""

_original_send_decision_email = email_service.send_submission_decision_email
_original_send_form_notification_email = email_service.send_form_submission_notification_email


def _app_module() -> Any:
    return sys.modules.get("app") or sys.modules.get("__main__")


def _clean_host(value: str) -> str:
    return str(value or "").strip().strip("'\"").strip()


def _normalize_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().strip("/")


def _get_form_definition_for_title(app_module: Any, form_title: str) -> dict:
    title = str(form_title or "").strip()
    if not title or not hasattr(app_module, "get_forms") or not hasattr(app_module, "get_form_definition"):
        return {}

    try:
        for form in app_module.get_forms():
            slug = form.get("slug", "")
            candidate_title = str(form.get("title", "")).strip()
            if title == candidate_title or title == slug:
                return app_module.get_form_definition(slug) or {}
    except Exception as exc:
        logger.warning("Cannot resolve form definition for email footer: %s", exc)

    return {}


def _current_form_definition() -> dict:
    app_module = _app_module()
    if app_module is None:
        return {}
    return _get_form_definition_for_title(app_module, _CURRENT_FORM_TITLE)


def _footer_logo_from_form_definition(form_definition: dict) -> str:
    footer_config = form_definition.get("email_footer") or {}

    if isinstance(footer_config, dict):
        for key in ("logo", "logo_path", "image", "image_path"):
            path = _normalize_path(footer_config.get(key))
            if path:
                return path

    for key in ("email_footer_logo", "email_footer_logo_path", "footer_logo", "footer_logo_path"):
        path = _normalize_path(form_definition.get(key))
        if path:
            return path

    return ""


def _footer_logo_width_from_form_definition(form_definition: dict) -> int:
    footer_config = form_definition.get("email_footer") or {}
    raw_value = ""

    if isinstance(footer_config, dict):
        raw_value = footer_config.get("logo_width") or footer_config.get("logo_width_px") or ""

    if not raw_value:
        raw_value = form_definition.get("email_footer_logo_width") or form_definition.get("footer_logo_width") or ""

    try:
        width = int(str(raw_value).replace("px", "").strip()) if raw_value else 420
    except ValueError:
        width = 420

    return max(120, min(width, 700))


def _footer_text_from_form_definition(form_definition: dict) -> str:
    footer_config = form_definition.get("email_footer") or {}

    if isinstance(footer_config, dict):
        for key in ("text", "content", "description"):
            value = str(footer_config.get(key, "")).strip()
            if value:
                return value

    for key in ("email_footer_text", "email_footer_content", "footer_text", "footer_content"):
        value = str(form_definition.get(key, "")).strip()
        if value:
            return value

    return "Wiadomość została wygenerowana automatycznie przez system formularzy."


def _resolve_logo_path(app_module: Any) -> str:
    form_definition = _current_form_definition()
    configured = _footer_logo_from_form_definition(form_definition)

    if not configured:
        configured = _normalize_path(app_module.app.config.get("EMAIL_FOOTER_LOGO_PATH", ""))

    if not configured:
        configured = "Logo/logo.png"

    forms_dir = _normalize_path(app_module.app.config.get("NEXTCLOUD_FORMS_DIR", "Formularze")) or "Formularze"
    output_dir = _normalize_path(app_module.app.config.get("NEXTCLOUD_OUTPUT_DIR", "output")) or "output"

    if configured.startswith((forms_dir + "/", output_dir + "/")):
        return configured

    return f"{forms_dir}/{configured}"


def _read_logo() -> tuple[bytes | None, str]:
    app_module = _app_module()
    if app_module is None or not hasattr(app_module, "storage"):
        return None, "png"

    path = _resolve_logo_path(app_module)
    if not path:
        return None, "png"

    try:
        logo_bytes = app_module.storage.read_bytes(path)
        mime_type, _ = mimetypes.guess_type(Path(path).name)
        subtype = (mime_type or "image/png").split("/", 1)[-1]
        logger.info("Loaded email footer logo from Nextcloud: %s", path)
        return logo_bytes, subtype
    except Exception as exc:
        logger.warning("Cannot load email footer logo from Nextcloud path %r: %s", path, exc)
        return None, "png"


def _append_footer(html_body: str, has_logo: bool) -> str:
    form_definition = _current_form_definition()
    footer_text = html.escape(_footer_text_from_form_definition(form_definition)).replace("\n", "<br>")
    logo_width = _footer_logo_width_from_form_definition(form_definition)

    logo = ""
    if has_logo:
        logo = '<div style="margin-top:12px"><img src="cid:%s" alt="Logo" style="max-width:%spx;width:100%%;height:auto;display:block"></div>' % (FOOTER_CID, logo_width)

    footer = (
        '<hr style="border:none;border-top:1px solid #d8d8d8;margin:24px 0 12px 0">'
        '<div style="font-size:12px;line-height:1.45;color:#555">'
        f'<p style="margin:0 0 8px 0">{footer_text}</p>'
        + logo +
        '</div>'
    )

    lower_html = html_body.lower()
    if "</body>" in lower_html:
        index = lower_html.rfind("</body>")
        return html_body[:index] + footer + html_body[index:]

    return html_body + footer


def _send_email_with_footer(
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    mail_from: str,
    to_emails: list[str],
    subject: str,
    html_body: str,
    text_body: str,
    use_tls: bool = True,
    use_ssl: bool = False,
    timeout: int = 30,
) -> None:
    smtp_host = _clean_host(smtp_host)
    smtp_user = str(smtp_user or "").strip()
    mail_from = str(mail_from or "").strip()

    if not smtp_host or not smtp_user or not smtp_password or not mail_from:
        raise RuntimeError("Brak konfiguracji SMTP.")

    recipients = [email.strip() for email in to_emails if str(email).strip()]
    if not recipients:
        raise RuntimeError("Brak odbiorców wiadomości e-mail.")

    logo_bytes, logo_subtype = _read_logo()
    html_body = _append_footer(html_body, bool(logo_bytes))

    message = EmailMessage()
    message["From"] = mail_from
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    if logo_bytes:
        html_part = message.get_payload()[-1]
        html_part.add_related(
            logo_bytes,
            maintype="image",
            subtype=logo_subtype or "png",
            cid=f"<{FOOTER_CID}>",
            filename=f"footer-logo.{logo_subtype or 'png'}",
        )

    smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    logger.info("SMTP connect host=%r port=%s ssl=%s tls=%s", smtp_host, smtp_port, use_ssl, use_tls)

    with smtp_class(smtp_host, smtp_port, timeout=timeout) as smtp:
        if use_tls and not use_ssl:
            smtp.starttls()
        smtp.login(smtp_user, smtp_password)
        smtp.send_message(message)


def _send_submission_decision_email_with_form_context(*args, **kwargs):
    global _CURRENT_FORM_TITLE
    previous_title = _CURRENT_FORM_TITLE
    _CURRENT_FORM_TITLE = str(kwargs.get("form_title", "") or "")
    try:
        return _original_send_decision_email(*args, **kwargs)
    finally:
        _CURRENT_FORM_TITLE = previous_title


def _send_form_submission_notification_email_with_form_context(*args, **kwargs):
    global _CURRENT_FORM_TITLE
    previous_title = _CURRENT_FORM_TITLE
    _CURRENT_FORM_TITLE = str(kwargs.get("form_title", "") or "")
    try:
        return _original_send_form_notification_email(*args, **kwargs)
    finally:
        _CURRENT_FORM_TITLE = previous_title


email_service._send_email = _send_email_with_footer
email_service.send_submission_decision_email = _send_submission_decision_email_with_form_context
email_service.send_form_submission_notification_email = _send_form_submission_notification_email_with_form_context
