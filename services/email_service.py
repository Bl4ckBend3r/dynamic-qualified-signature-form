import logging
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
    timeout: int = 30,
    sender_name: str = "",
    reply_to: str = "",
) -> None:
    smtp_host = _normalize_smtp_host(smtp_host)
    smtp_user = str(smtp_user or "").strip()
    mail_from = str(mail_from or "").strip()

    if not smtp_host or not mail_from:
        raise RuntimeError("Brak konfiguracji SMTP.")

    recipients = [email.strip() for email in to_emails if str(email).strip()]

    if not recipients:
        raise RuntimeError("Brak odbiorców wiadomości e-mail.")

    message = EmailMessage()
    message["From"] = formataddr((str(sender_name or "").strip(), mail_from)) if sender_name else mail_from
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    if str(reply_to or "").strip():
        message["Reply-To"] = str(reply_to).strip()
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    logger.info("SMTP connect host=%r port=%s ssl=%s tls=%s", smtp_host, smtp_port, use_ssl, use_tls)

    with smtp_class(smtp_host, smtp_port, timeout=timeout) as smtp:
        if use_tls and not use_ssl:
            smtp.starttls()
        if smtp_user and smtp_password:
            smtp.login(smtp_user, smtp_password)
        smtp.send_message(message)
