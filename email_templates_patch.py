from __future__ import annotations

import html
import logging
import re
import sys
from typing import Any

import email_footer_patch
from services import email_service

logger = logging.getLogger(__name__)

_previous_submission_email = email_service.send_form_submission_notification_email
_previous_decision_email = email_service.send_submission_decision_email


def _app_module() -> Any:
    return sys.modules.get("app") or sys.modules.get("__main__")


def _normalize_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().strip("/")


def _resolve_nextcloud_path(path: str) -> str:
    app_module = _app_module()
    normalized = _normalize_path(path)

    if not normalized or app_module is None:
        return normalized

    forms_dir = _normalize_path(app_module.app.config.get("NEXTCLOUD_FORMS_DIR", "Formularze")) or "Formularze"
    output_dir = _normalize_path(app_module.app.config.get("NEXTCLOUD_OUTPUT_DIR", "output")) or "output"

    if normalized.startswith((forms_dir + "/", output_dir + "/")):
        return normalized

    return f"{forms_dir}/{normalized}"


def _get_form_definition(form_title: str) -> dict:
    app_module = _app_module()
    title = str(form_title or "").strip()

    if app_module is None or not title:
        return {}

    try:
        for form in app_module.get_forms():
            slug = form.get("slug", "")
            candidate_title = str(form.get("title", "")).strip()
            if title == candidate_title or title == slug:
                return app_module.get_form_definition(slug) or {}
    except Exception as exc:
        logger.warning("Cannot resolve form definition for email template: %s", exc)

    return {}


def _template_config(form_definition: dict, key: str) -> dict:
    config = form_definition.get("email_templates") or form_definition.get("emails") or {}
    item = config.get(key, {}) if isinstance(config, dict) else {}

    if isinstance(item, str):
        return {"template": item}

    if isinstance(item, dict):
        return item

    return {}


def _read_template(path: str) -> str:
    app_module = _app_module()
    resolved = _resolve_nextcloud_path(path)

    if app_module is None or not resolved:
        return ""

    try:
        template = app_module.storage.read_text_or_empty(resolved)
        if template.strip():
            logger.info("Loaded email template from Nextcloud: %s", resolved)
        return template
    except Exception as exc:
        logger.warning("Cannot load email template from Nextcloud path %r: %s", resolved, exc)
        return ""


def _render_template(template: str, context: dict[str, Any]) -> str:
    result = template

    for key, value in context.items():
        raw_value = str(value or "")
        safe_value = html.escape(raw_value)
        result = result.replace("{{ " + key + " }}", safe_value)
        result = result.replace("{{" + key + "}}", safe_value)

    return result


def _html_to_text(value: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _with_form_footer_context(form_title: str, html_body: str) -> str:
    previous_title = email_footer_patch._CURRENT_FORM_TITLE
    email_footer_patch._CURRENT_FORM_TITLE = str(form_title or "")
    try:
        return email_footer_patch._append_footer(html_body, has_logo=True)
    finally:
        email_footer_patch._CURRENT_FORM_TITLE = previous_title


def _send_custom_email(
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    mail_from: str,
    to_emails: list[str],
    subject: str,
    html_body: str,
    form_title: str,
    use_tls: bool = True,
    use_ssl: bool = False,
    timeout: int = 30,
) -> None:
    html_body = _with_form_footer_context(form_title, html_body)

    email_service._send_email(
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_user=smtp_user,
        smtp_password=smtp_password,
        mail_from=mail_from,
        to_emails=to_emails,
        subject=subject,
        html_body=html_body,
        text_body=_html_to_text(html_body),
        use_tls=use_tls,
        use_ssl=use_ssl,
        timeout=timeout,
    )


def send_form_submission_notification_email(*args, **kwargs):
    form_title = str(kwargs.get("form_title", "") or "")
    form_definition = _get_form_definition(form_title)
    config = _template_config(form_definition, "submission_confirmation")
    template_path = _normalize_path(config.get("template") or config.get("html") or config.get("path"))

    if not template_path:
        return _previous_submission_email(*args, **kwargs)

    context = {
        "submission_id": kwargs.get("submission_id", ""),
        "form_title": form_title,
        "applicant_name": kwargs.get("applicant_name", ""),
        "applicant_email": kwargs.get("applicant_email", ""),
    }
    template = _read_template(template_path)

    if not template.strip():
        return _previous_submission_email(*args, **kwargs)

    subject = str(config.get("subject") or f"Potwierdzenie wysłania formularza - {form_title}")
    html_body = _render_template(template, context)

    return _send_custom_email(
        smtp_host=kwargs["smtp_host"],
        smtp_port=kwargs["smtp_port"],
        smtp_user=kwargs["smtp_user"],
        smtp_password=kwargs["smtp_password"],
        mail_from=kwargs["mail_from"],
        to_emails=kwargs["to_emails"],
        subject=subject,
        html_body=html_body,
        form_title=form_title,
        use_tls=kwargs.get("use_tls", True),
        use_ssl=kwargs.get("use_ssl", False),
        timeout=kwargs.get("timeout", 30),
    )


def send_submission_decision_email(*args, **kwargs):
    form_title = str(kwargs.get("form_title", "") or "")
    accepted = bool(kwargs.get("accepted"))
    template_key = "decision_accepted" if accepted else "decision_rejected"
    form_definition = _get_form_definition(form_title)
    config = _template_config(form_definition, template_key)
    template_path = _normalize_path(config.get("template") or config.get("html") or config.get("path"))

    if not template_path:
        return _previous_decision_email(*args, **kwargs)

    context = {
        "submission_id": kwargs.get("submission_id", ""),
        "form_title": form_title,
        "decision": "zaakceptowany" if accepted else "odrzucony",
    }
    template = _read_template(template_path)

    if not template.strip():
        return _previous_decision_email(*args, **kwargs)

    default_subject = "Wniosek zaakceptowany - dokumenty do podpisu" if accepted else "Wniosek nie został zaakceptowany"
    subject = str(config.get("subject") or default_subject)
    html_body = _render_template(template, context)

    return _send_custom_email(
        smtp_host=kwargs["smtp_host"],
        smtp_port=kwargs["smtp_port"],
        smtp_user=kwargs["smtp_user"],
        smtp_password=kwargs["smtp_password"],
        mail_from=kwargs["mail_from"],
        to_emails=[kwargs["to_email"]],
        subject=subject,
        html_body=html_body,
        form_title=form_title,
        use_tls=kwargs.get("use_tls", True),
        use_ssl=kwargs.get("use_ssl", False),
        timeout=kwargs.get("timeout", 30),
    )


email_service.send_form_submission_notification_email = send_form_submission_notification_email
email_service.send_submission_decision_email = send_submission_decision_email
