from __future__ import annotations

from flask import current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from models import EmailLog, PlatformMailTemplate, SystemMailSettings
from services.mail_settings_service import diagnose_smtp_error
from services.mail_template_service import sanitize_content_html

from . import (
    bp,
    db_session_factory,
    ensure_form_access,
    list_active_logos,
    login_required,
    permission_required,
)


@bp.route("/mail-settings", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_site")
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
@permission_required("can_manage_site")
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
        except Exception as exc:
            diagnostic = diagnose_smtp_error(exc)
            _log_smtp_failure(config or {}, form=None, diagnostic=diagnostic)
            _save_smtp_attempt(db, form=None, status="failed", diagnostic=diagnostic)
            flash(diagnostic.administrator_message, "error")
        else:
            _log_smtp_success(config or {}, form=None)
            if _save_smtp_attempt(db, form=None, status="sent"):
                flash("Połączenie SMTP zakończyło się powodzeniem. Nie wysłano wiadomości.", "success")
            else:
                flash(
                    "Połączenie SMTP działa, ale nie udało się zapisać logu testu. "
                    "Sprawdź migracje bazy danych.",
                    "error",
                )
    return redirect(url_for("admin.system_mail_settings"))


@bp.post("/mail-settings/test-email")
@login_required
@permission_required("can_manage_site")
def system_mail_settings_test_email():
    settings_service = current_app.extensions["services"].mail_settings_service
    dispatch_service = current_app.extensions["services"].mail_dispatch_service
    with db_session_factory()() as db:
        settings = db.execute(select(SystemMailSettings).order_by(SystemMailSettings.id)).scalars().first()
        if not settings:
            flash("Najpierw zapisz domyślną konfigurację SMTP.", "error")
            return redirect(url_for("admin.system_mail_settings"))
        config = settings_service.resolve_smtp(
            db,
            type("SystemForm", (), {"mail_mode": "system"})(),
            current_app.config,
        )
        recipient = str(g.admin_user.email or "").strip()
        try:
            if not recipient:
                raise ValueError("Brak adresu e-mail administratora.")
            footer = dispatch_service.mail_footer_resolver.resolve(db, mail_type="system")
            logo_url, inline_images = dispatch_service.footer_logo_for_email(db, footer)
            footer_html = dispatch_service.build_footer(footer, logo_url=logo_url)
            html_body = (
                "<html><body><p>To jest testowa wiadomość konfiguracji SMTP.</p>"
                f'<footer class="mail-footer">{footer_html}</footer></body></html>'
            )
            sender = dispatch_service.smtp_sender or getattr(dispatch_service.notification_service, "smtp_sender", None)
            if sender is None:
                raise RuntimeError("Brak adaptera SMTP.")
            sender(
                **(config or {}),
                to_emails=[recipient],
                subject="Test konfiguracji SMTP",
                html_body=html_body,
                text_body="To jest testowa wiadomość konfiguracji SMTP.",
                inline_images=inline_images,
            )
        except Exception as exc:
            diagnostic = diagnose_smtp_error(exc)
            _log_smtp_failure(config or {}, form=None, diagnostic=diagnostic)
            _save_smtp_attempt(
                db,
                form=None,
                status="failed",
                diagnostic=diagnostic,
                to_email=recipient,
                subject="Testowa wiadomość SMTP",
            )
            flash(diagnostic.administrator_message, "error")
        else:
            _log_smtp_success(config or {}, form=None)
            saved = _save_smtp_attempt(
                db,
                form=None,
                status="sent",
                to_email=recipient,
                subject="Testowa wiadomość SMTP",
                success_message="Wiadomość testowa SMTP została wysłana.",
            )
            if saved:
                flash(f"Wysłano wiadomość testową SMTP do {recipient}.", "success")
            else:
                flash(
                    "Wiadomość testowa SMTP została wysłana, ale nie udało się "
                    "zapisać logu testu. Sprawdź migracje bazy danych.",
                    "error",
                )
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
        except Exception as exc:
            diagnostic = diagnose_smtp_error(exc)
            _log_smtp_failure(config or {}, form=form, diagnostic=diagnostic)
            _save_smtp_attempt(db, form=form, status="failed", diagnostic=diagnostic)
            flash(diagnostic.administrator_message, "error")
        else:
            _log_smtp_success(config or {}, form=form)
            if _save_smtp_attempt(db, form=form, status="sent"):
                flash("Połączenie SMTP zakończyło się powodzeniem. Nie wysłano wiadomości.", "success")
            else:
                flash(
                    "Połączenie SMTP działa, ale nie udało się zapisać logu testu. "
                    "Sprawdź migracje bazy danych.",
                    "error",
                )
    return redirect(url_for("admin.form_edit", form_id=form_id, tab="emails"))


def _smtp_error_message(exc: Exception) -> str:
    return diagnose_smtp_error(exc).administrator_message


def _log_smtp_attempt(
    db,
    *,
    form,
    status: str,
    diagnostic=None,
    to_email: str = "",
    subject: str = "Test połączenia SMTP",
    success_message: str = "Połączenie SMTP zakończyło się powodzeniem.",
) -> None:
    administrator_message = (
        diagnostic.administrator_message
        if diagnostic
        else success_message
    )
    db.add(
        EmailLog(
            form_id=getattr(form, "id", None),
            public_submission_id="",
            to_email=to_email,
            subject=subject,
            status=status,
            event_type="smtp_test",
            error_type=diagnostic.error_type if diagnostic else "",
            administrator_message=administrator_message,
            error_message=administrator_message if diagnostic else "",
            sent_by_id=g.admin_user.id,
        )
    )


def _save_smtp_attempt(db, **values) -> bool:
    try:
        _log_smtp_attempt(db, **values)
        db.commit()
        return True
    except SQLAlchemyError:
        db.rollback()
        current_app.logger.exception(
            "Nie udało się zapisać logu testu SMTP. "
            "Sprawdź migracje tabeli email_logs."
        )
        return False


def _smtp_log_values(config) -> tuple[str, int, bool, bool, int]:
    return (
        str(config.get("smtp_host") or config.get("host") or "").strip(),
        int(config.get("smtp_port") or config.get("port") or 587),
        bool(config.get("use_tls")),
        bool(config.get("use_ssl")),
        int(config.get("timeout") or current_app.config.get("SMTP_TIMEOUT", 10)),
    )


def _log_smtp_failure(config, *, form, diagnostic) -> None:
    host, port, use_tls, use_ssl, timeout = _smtp_log_values(config)
    current_app.logger.warning(
        "smtp_connection_test_failed error_type=%s host=%s port=%s tls=%s ssl=%s timeout=%s form_id=%s",
        diagnostic.error_type,
        host,
        port,
        use_tls,
        use_ssl,
        timeout,
        getattr(form, "id", None),
    )


def _log_smtp_success(config, *, form) -> None:
    host, port, use_tls, use_ssl, timeout = _smtp_log_values(config)
    current_app.logger.info(
        "smtp_connection_test_succeeded host=%s port=%s tls=%s ssl=%s timeout=%s form_id=%s",
        host,
        port,
        use_tls,
        use_ssl,
        timeout,
        getattr(form, "id", None),
    )
