import logging
import re
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

logger = logging.getLogger(__name__)


def _as_bool(value: str | bool | None, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "tak", "on"}


def _normalize_smtp_host(value: str) -> str:
    return str(value or "").strip().strip("'\"").strip()


def _send_email(
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
    timeout: int = 10,
    sender_name: str = "",
    reply_to: str = "",
    inline_images: list[dict] | None = None,
    attachments=None,
) -> None:
    smtp_host = _normalize_smtp_host(smtp_host)
    smtp_user = str(smtp_user or "").strip()
    mail_from = str(mail_from or "").strip()

    if not smtp_host or not mail_from:
        raise RuntimeError("Brak konfiguracji SMTP.")

    recipients = [email.strip() for email in to_emails if str(email).strip()]

    if not recipients:
        raise RuntimeError("Brak odbiorców wiadomości e-mail.")

    valid_inline_images = []
    for image in inline_images or []:
        content = image.get("content")
        content_id = str(image.get("cid") or "").strip().strip("<>")
        mime_type = str(image.get("mime_type") or "").strip().lower()
        if isinstance(content, bytes) and content and content_id and mime_type.startswith("image/"):
            valid_inline_images.append({**image, "cid": content_id, "mime_type": mime_type})
    valid_cids = {image["cid"] for image in valid_inline_images}
    cid_image_pattern = re.compile(
        r"<img\b[^>]*\bsrc=[\"']cid:([^\"']+)[\"'][^>]*>",
        flags=re.IGNORECASE,
    )
    html_body = cid_image_pattern.sub(
        lambda match: match.group(0) if match.group(1) in valid_cids else "",
        html_body,
    )

    def build_message(rendered_html: str) -> EmailMessage:
        built = EmailMessage()
        built["From"] = formataddr((str(sender_name or "").strip(), mail_from)) if sender_name else mail_from
        built["To"] = ", ".join(recipients)
        built["Subject"] = subject
        if str(reply_to or "").strip():
            built["Reply-To"] = str(reply_to).strip()
        built.set_content(text_body)
        built.add_alternative(rendered_html, subtype="html")
        return built

    message = build_message(html_body)
    try:
        html_part = message.get_payload()[-1]
        for image in valid_inline_images:
            content = image.get("content")
            content_id = image["cid"]
            mime_type = image["mime_type"]
            _, subtype = mime_type.split("/", 1)
            html_part.add_related(
                content,
                maintype="image",
                subtype=subtype,
                cid=f"<{content_id}>",
                filename=str(image.get("filename") or "logo"),
                disposition="inline",
            )
    except (AttributeError, IndexError, TypeError, ValueError):
        logger.warning("Nie udało się osadzić logo inline; wiadomość zostanie wysłana bez logo.")
        html_without_cid_images = cid_image_pattern.sub("", html_body)
        message = build_message(html_without_cid_images)

    smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    logger.info("SMTP connect host=%r port=%s ssl=%s tls=%s", smtp_host, smtp_port, use_ssl, use_tls)

    with smtp_class(smtp_host, smtp_port, timeout=timeout) as smtp:
        if use_tls and not use_ssl:
            smtp.starttls()
        if smtp_user and smtp_password:
            smtp.login(smtp_user, smtp_password)
        
        for attachment in attachments or []:
            content_type = attachment.get("content_type") or "application/pdf"
            maintype, _, subtype = content_type.partition("/")

            message.add_attachment(
                attachment["content"],
                maintype=maintype or "application",
                subtype=subtype or "pdf",
                filename=attachment["filename"],
            )
        smtp.send_message(message)
