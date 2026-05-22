from __future__ import annotations

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


def _app_module() -> Any:
    return sys.modules.get("app") or sys.modules.get("__main__")


def _clean_host(value: str) -> str:
    return str(value or "").strip().strip("'\"").strip()


def _resolve_logo_path(app_module: Any) -> str:
    configured = str(app_module.app.config.get("EMAIL_FOOTER_LOGO_PATH", "Logo/logo.png") or "").replace("\\", "/").strip().strip("/")
    if not configured:
        return ""

    forms_dir = str(app_module.app.config.get("NEXTCLOUD_FORMS_DIR", "Formularze") or "Formularze").strip("/")
    output_dir = str(app_module.app.config.get("NEXTCLOUD_OUTPUT_DIR", "output") or "output").strip("/")

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
    logo = ""
    if has_logo:
        logo = '<div style="margin-top:12px"><img src="cid:%s" alt="Logo" style="max-width:260px;height:auto;display:block"></div>' % FOOTER_CID

    footer = (
        '<hr style="border:none;border-top:1px solid #d8d8d8;margin:24px 0 12px 0">'
        '<div style="font-size:12px;line-height:1.45;color:#555">'
        '<p style="margin:0 0 8px 0">Wiadomość została wygenerowana automatycznie przez system formularzy.</p>'
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


email_service._send_email = _send_email_with_footer
