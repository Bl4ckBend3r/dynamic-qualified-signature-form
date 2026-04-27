import smtplib
from email.message import EmailMessage


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
) -> None:
    if not smtp_host or not smtp_user or not smtp_password or not mail_from:
        raise RuntimeError("Brak konfiguracji SMTP.")

    subject = (
        "Wniosek zaakceptowany - dokumenty do podpisu"
        if accepted
        else "Wniosek nie został zaakceptowany"
    )

    message = EmailMessage()
    message["From"] = mail_from
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(smtp_host, smtp_port) as smtp:
        smtp.starttls()
        smtp.login(smtp_user, smtp_password)
        smtp.send_message(message)