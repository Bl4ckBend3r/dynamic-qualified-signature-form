import logging
import smtplib
from email.message import EmailMessage

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
) -> None:
    smtp_host = _normalize_smtp_host(smtp_host)
    smtp_user = str(smtp_user or "").strip()
    mail_from = str(mail_from or "").strip()

    if not smtp_host or not smtp_user or not smtp_password or not mail_from:
        raise RuntimeError("Brak konfiguracji SMTP.")

    recipients = [email.strip() for email in to_emails if str(email).strip()]

    if not recipients:
        raise RuntimeError("Brak odbiorców wiadomości e-mail.")

    message = EmailMessage()
    message["From"] = mail_from
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    logger.info("SMTP connect host=%r port=%s ssl=%s tls=%s", smtp_host, smtp_port, use_ssl, use_tls)

    with smtp_class(smtp_host, smtp_port, timeout=timeout) as smtp:
        if use_tls and not use_ssl:
            smtp.starttls()
        smtp.login(smtp_user, smtp_password)
        smtp.send_message(message)


def send_submission_decision_email(
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    mail_from: str,
    to_email: str,
    submission_id: str,
    form_title: str,
    accepted: bool,
    html_body: str,
    text_body: str,
    use_tls: bool = True,
    use_ssl: bool = False,
    timeout: int = 30,
) -> None:
    subject = (
        "Wniosek zaakceptowany - dokumenty do podpisu"
        if accepted
        else "Wniosek nie został zaakceptowany"
    )

    _send_email(
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_user=smtp_user,
        smtp_password=smtp_password,
        mail_from=mail_from,
        to_emails=[to_email],
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        use_tls=use_tls,
        use_ssl=use_ssl,
        timeout=timeout,
    )


def send_form_submission_notification_email(
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    mail_from: str,
    to_emails: list[str],
    submission_id: str,
    form_title: str,
    applicant_name: str = "",
    applicant_email: str = "",
    use_tls: bool = True,
    use_ssl: bool = False,
    timeout: int = 30,
) -> None:
    subject = f"Nowe zgłoszenie z formularza - {form_title}"

    applicant_line = applicant_name or "Nie podano"
    applicant_email_line = applicant_email or "Nie podano"

    text_body = (
        "Dzień dobry,\n\n"
        "w systemie formularzy zostało zapisane nowe zgłoszenie.\n\n"
        f"Formularz: {form_title}\n"
        f"ID zgłoszenia: {submission_id}\n"
        f"Kandydat/ka: {applicant_line}\n"
        f"E-mail kandydata/ki: {applicant_email_line}\n\n"
        "Zgłoszenie oraz wygenerowany PDF są dostępne w katalogu wynikowym formularza.\n\n"
        "Pozdrawiamy\n"
    )

    html_body = f"""
    <p>Dzień dobry,</p>
    <p>W systemie formularzy zostało zapisane nowe zgłoszenie.</p>
    <table cellpadding="6" cellspacing="0" border="0">
        <tr><td><strong>Formularz:</strong></td><td>{form_title}</td></tr>
        <tr><td><strong>ID zgłoszenia:</strong></td><td>{submission_id}</td></tr>
        <tr><td><strong>Kandydat/ka:</strong></td><td>{applicant_line}</td></tr>
        <tr><td><strong>E-mail kandydata/ki:</strong></td><td>{applicant_email_line}</td></tr>
    </table>
    <p>Zgłoszenie oraz wygenerowany PDF są dostępne w katalogu wynikowym formularza.</p>
    <p>Pozdrawiamy</p>
    """

    _send_email(
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_user=smtp_user,
        smtp_password=smtp_password,
        mail_from=mail_from,
        to_emails=to_emails,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        use_tls=use_tls,
        use_ssl=use_ssl,
        timeout=timeout,
    )
