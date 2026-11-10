from __future__ import annotations

from types import SimpleNamespace

from flask import current_app, flash, g, redirect, render_template, request, url_for
from jinja2 import TemplateError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from models import EmailLog, PlatformMailTemplate, SystemMailSettings
from services.mail_settings_service import diagnose_smtp_error
from services.mail_template_service import html_to_text, sanitize_content_html

from flask import abort
from services.system_mail_registry import (
    SYSTEM_MAIL_TYPES,
    SYSTEM_MAIL_BY_ID,
    load_default_template,
    SystemMailTemplateError,
    system_mail_variables,
)
from services.mail_template_service import (
    render_platform_mail_html,
    render_platform_mail_text,
    render_template_text,
)

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

@bp.get("/mail-settings/mails")
@login_required
@permission_required("can_manage_site")
def system_mail_mails():
    return render_template("admin/mail_settings/mails.html")


@bp.get("/mail-settings/system-templates")
@login_required
@permission_required("can_manage_site")
def system_mail_templates():
    with db_session_factory()() as db:
        saved = db.execute(
            select(PlatformMailTemplate)
        ).scalars().all()

        saved_by_type = {
            template.template_type: template
            for template in saved
        }

        items = []

        for mail_type in SYSTEM_MAIL_TYPES:
            error = None

            try:
                load_default_template(mail_type.id)
            except SystemMailTemplateError as exc:
                error = str(exc)

            items.append({
                "type": mail_type,
                "saved": saved_by_type.get(mail_type.id),
                "error": error,
            })

    return render_template(
        "admin/mail_settings/system_templates.html",
        items=items,
    )

@bp.route("/mail-settings/system-templates/<string:mail_type_id>", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_site")
def system_mail_template_detail(mail_type_id):
    mail_type = SYSTEM_MAIL_BY_ID.get(mail_type_id)
    if mail_type is None:
        abort(404)

    try:
        template = load_default_template(mail_type_id)
    except SystemMailTemplateError:
        current_app.logger.exception(
            "system_mail_template_load_failed type=%s",
            mail_type_id,
        )
        abort(503, description="Nie można odczytać szablonu.")

    variables = system_mail_variables(mail_type_id)
    categories = dict.fromkeys(variable.category for variable in variables)
    for category in categories:
        categories[category] = tuple(variable for variable in variables if variable.category == category)
    context = {variable.name: variable.example for variable in variables}
    context.update(template.context_defaults)
    context.update(
        mail_details=[("Numer zgłoszenia", "ZGŁ-2026-001"),
                      ("Formularz", "Wniosek o szkolenie"), ("Data", "10.11.2026")],
        survey_description_lines=[context.get("survey_description", "")],
        session_name_lines=[context.get("session_name", "")],
        mail_message_lines=[],
    )
    error = ""

    with db_session_factory()() as db:
        saved = db.execute(
            select(PlatformMailTemplate).where(
                PlatformMailTemplate.template_type == mail_type_id
            )
        ).scalar_one_or_none()

        if request.method == "POST":
            if request.form.get("action") == "restore":
                if saved is not None:
                    db.delete(saved)
                    db.commit()
                flash("Przywrócono domyślny szablon z plików.", "success")
                return redirect(url_for("admin.system_mail_template_detail", mail_type_id=mail_type_id))
            if request.form.get("action") != "save":
                abort(400)
            subject = request.form.get("subject", "").strip()
            html_body = sanitize_content_html(request.form.get("html_body", "").strip())
            text_body = request.form.get("text_body", "").strip()
            if not subject or len(subject) > 500 or not html_to_text(html_body).strip() or not text_body:
                error = "Wypełnij temat (maksymalnie 500 znaków) oraz treść HTML i TXT."
            else:
                try:
                    render_template_text(subject, context)
                    render_template_text(html_body, context, escape_html=True)
                    render_template_text(text_body, context)
                except TemplateError:
                    error = "Szablon zawiera niepoprawną składnię zmiennych."
            if not error:
                if saved is None:
                    saved = PlatformMailTemplate(template_type=mail_type_id,
                                                 name=template.content_title or mail_type.name)
                    db.add(saved)
                saved.subject = subject
                saved.html_body = html_body
                saved.text_body = text_body
                saved.is_active = True
                saved.updated_by_user_id = g.admin_user.id
                db.commit()
                flash("Szablon wiadomości został zapisany.", "success")
                return redirect(url_for("admin.system_mail_template_detail", mail_type_id=mail_type_id))

        layout = current_app.extensions[
            "services"
        ].mail_settings_service.get_layout(db)

    active_template = saved or template
    fields = {key: request.form.get(key, "") for key in ("subject", "html_body", "text_body")} if error else {
        "subject": active_template.subject,
        "html_body": active_template.html_body,
        "text_body": active_template.text_body,
    }
    preview_template = SimpleNamespace(
        name=active_template.name, subject=fields["subject"],
        html_body=fields["html_body"], text_body=fields["text_body"],
        content_title=template.content_title if saved is None else "",
        mail_action_text_template=template.mail_action_text_template,
        mail_action_link_label_template=template.mail_action_link_label_template,
    )
    for key, attribute in (("mail_action_text", "mail_action_text_template"),
                           ("mail_action_link_label", "mail_action_link_label_template")):
        if getattr(preview_template, attribute, ""):
            context[key] = render_template_text(getattr(preview_template, attribute), context)
    try:
        preview_html = render_platform_mail_html(preview_template, context, layout=layout)
        render_platform_mail_text(preview_template, context)
        preview_subject = render_template_text(preview_template.subject, context)
    except TemplateError:
        preview_html = "<p>Nie można wyświetlić podglądu tego szablonu.</p>"
        preview_subject = ""

    return render_template(
        "admin/mail_settings/system_template_detail.html",
        mail_type=mail_type,
        template=template,
        saved=saved,
        fields=fields,
        variable_categories=categories,
        error=error,
        preview_html=preview_html,
        preview_subject=preview_subject,
    ), (422 if error else 200)

@bp.route("/mail-settings/layout", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_site")
def system_mail_layout():
    from services.mail_template_service import render_platform_mail_html
    from models import Logo

    service = current_app.extensions["services"].mail_settings_service

    with db_session_factory()() as db:
        layout = service.get_layout(db)
        logos = list_active_logos(db)

        if request.method == "POST":
            settings = service.get_or_create_system_settings(db)

            form_data = request.form.to_dict()
            form_data["layout_footer_html"] = layout.get("footer_html", "")

            logo_id = str(form_data.get("layout_logo_id") or "").strip()
            if logo_id and logo_id not in {str(logo.id) for logo in logos}:
                flash("Wybierz aktywne logo.", "error")
                return redirect(url_for("admin.system_mail_layout"))

            settings.layout_config = {
                **(settings.layout_config or {}),
                **service.layout_from_form(form_data),
            }
            settings.updated_by_user_id = g.admin_user.id

            db.commit()
            flash("Układ wiadomości został zapisany.", "success")
            return redirect(url_for("admin.system_mail_layout"))

        preview_layout = dict(layout)

        if layout.get("logo_id") and layout.get("logo_position") != "none":
            logo = db.get(Logo, layout["logo_id"])
            if logo and logo.active:
                preview_layout["logo_url"] = url_for(
                    "public_forms.logo_asset",
                    logo_id=logo.id,
                    filename=logo.filename,
                    _external=True,
                )

        sample = load_default_template("submission_received")

        context = {
            "mail_details": [
                ("Numer zgłoszenia", "TEST-2026-001"),
                ("Formularz", "Przykładowy formularz"),
                ("Data", "10.11.2026"),
            ]
        }

        preview_html = render_platform_mail_html(
            sample, context, layout=preview_layout
        )

        return render_template(
            "admin/mail_settings/layout.html",
            layout=layout,
            logos=logos,
            preview_html=preview_html,
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
