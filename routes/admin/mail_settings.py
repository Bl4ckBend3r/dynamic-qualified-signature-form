from __future__ import annotations

import smtplib

from flask import current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select

from models import EmailLog, PlatformMailTemplate, SystemMailSettings
from services.mail_template_service import sanitize_content_html

from . import (
    ROLE_SUPER_ADMIN,
    bp,
    db_session_factory,
    ensure_form_access,
    list_active_logos,
    login_required,
    role_required,
)


@bp.route("/mail-settings", methods=["GET", "POST"])
@login_required
@role_required(ROLE_SUPER_ADMIN)
def system_mail_settings():
    service = current_app.extensions["services"].mail_settings_service
    with db_session_factory()() as db:
        settings = db.execute(select(SystemMailSettings).order_by(SystemMailSettings.id)).scalars().first()
        template = db.execute(
            select(PlatformMailTemplate).where(PlatformMailTemplate.template_type == "submission_received")
        ).scalar_one_or_none()
        if request.method == "POST":
            settings = settings or service.get_or_create_system_settings(db)
            service.update_system(settings, request.form, updated_by_user_id=g.admin_user.id)
            if template is None:
                template = PlatformMailTemplate(
                    template_type="submission_received",
                    name="Potwierdzenie otrzymania zgłoszenia",
                )
                db.add(template)
            template.subject = request.form.get("template_subject", "").strip()
            template.html_body = sanitize_content_html(request.form.get("template_html_body", "").strip())
            template.text_body = request.form.get("template_text_body", "").strip()
            template.is_active = request.form.get("template_is_active") == "on"
            template.updated_by_user_id = g.admin_user.id
            db.commit()
            flash("Domyślna poczta, szablon i layout zostały zapisane.", "success")
            return redirect(url_for("admin.system_mail_settings"))

        settings = settings or SystemMailSettings(smtp_config={}, layout_config={})
        template = template or PlatformMailTemplate(
            template_type="submission_received",
            name="Potwierdzenie otrzymania zgłoszenia",
            subject="",
            html_body="",
            text_body="",
            is_active=False,
        )
        return render_template(
            "admin/mail_settings/edit.html",
            settings=settings,
            smtp=service.public_config(settings.smtp_config),
            layout=service.get_layout(db),
            template=template,
            logos=list_active_logos(db),
            password_is_set=bool(settings.smtp_password_encrypted),
        )


@bp.post("/mail-settings/test")
@login_required
@role_required(ROLE_SUPER_ADMIN)
def system_mail_settings_test():
    service = current_app.extensions["services"].mail_settings_service
    with db_session_factory()() as db:
        settings = db.execute(select(SystemMailSettings).order_by(SystemMailSettings.id)).scalars().first()
        if not settings:
            flash("Najpierw zapisz domyślną konfigurację SMTP.", "error")
            return redirect(url_for("admin.system_mail_settings"))
        config = service.resolve_smtp(db, type("SystemForm", (), {"mail_mode": "system"})(), current_app.config)
        try:
            service.test_connection(config or {})
            flash("Połączenie SMTP zakończyło się powodzeniem. Nie wysłano wiadomości.", "success")
        except Exception as exc:
            current_app.logger.warning("smtp_connection_test_failed error=%s", exc.__class__.__name__)
            _log_smtp_failure(db, form=None, exc=exc)
            db.commit()
            flash(_smtp_error_message(exc), "error")
    return redirect(url_for("admin.system_mail_settings"))


@bp.post("/forms/<int:form_id>/mail-settings/test")
@login_required
def form_mail_settings_test(form_id: int):
    service = current_app.extensions["services"].mail_settings_service
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        config = service.resolve_smtp(db, form, current_app.config)
        try:
            service.test_connection(config or {})
            flash("Połączenie SMTP zakończyło się powodzeniem. Nie wysłano wiadomości.", "success")
        except Exception as exc:
            current_app.logger.warning("form_smtp_connection_test_failed form_id=%s error=%s", form.id, exc.__class__.__name__)
            _log_smtp_failure(db, form=form, exc=exc)
            db.commit()
            flash(_smtp_error_message(exc), "error")
    return redirect(url_for("admin.form_edit", form_id=form_id, tab="emails"))


def _smtp_error_message(exc: Exception) -> str:
    details = f"{type(exc).__name__}: {exc}".lower()
    if isinstance(exc, smtplib.SMTPAuthenticationError) or "auth" in details or "uwierzyteln" in details:
        return "Nie udało się zalogować do serwera SMTP. Sprawdź użytkownika, hasło i metodę szyfrowania."
    return "Nie udało się połączyć z serwerem SMTP. Sprawdź adres serwera, port i metodę szyfrowania."


def _log_smtp_failure(db, *, form, exc: Exception) -> None:
    db.add(
        EmailLog(
            form_id=getattr(form, "id", None),
            public_submission_id="",
            to_email="",
            subject="Test połączenia SMTP",
            status="failed",
            error_message=f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__,
            sent_by_id=g.admin_user.id,
        )
    )
