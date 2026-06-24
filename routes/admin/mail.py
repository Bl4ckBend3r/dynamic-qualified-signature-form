from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from flask import abort, current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select

from models import FormSubmission, MailFooter, MailTemplate, MailTemplateAsset
from services.mail_template_service import (
    MAIL_LAYOUT,
    MailImportError,
    generate_text_from_html,
    import_mail_template_zip,
    parse_mail_content,
    render_platform_mail_html,
)

from . import (
    MAIL_TEMPLATE_TYPES,
    OFFICER_DECISIONS,
    ROLE_SUPER_ADMIN,
    bp,
    db_session_factory,
    ensure_form_access,
    list_accessible_forms,
    login_required,
    list_active_logos,
    parse_optional_int,
    preview_mail_context,
    read_uploaded_template_file,
)


@bp.route("/forms/<int:form_id>/submissions/<int:submission_pk>/mail", methods=["GET", "POST"])
@login_required
def submission_mail(form_id: int, submission_pk: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id)
        submission = db.get(FormSubmission, submission_pk) or abort(404)
        if submission.form_slug != form.slug:
            abort(404)
        templates = db.execute(
            select(MailTemplate).where(MailTemplate.form_id == form.id, MailTemplate.is_active.is_(True)).order_by(MailTemplate.name)
        ).scalars().all()
        footers = db.execute(
            select(MailFooter).where(MailFooter.form_id == form.id, MailFooter.is_active.is_(True)).order_by(MailFooter.name)
        ).scalars().all()
        if request.method == "POST":
            result = send_admin_mail(db, form, submission, templates, footers)
            db.commit()
            flash(*mail_flash(result))
            return redirect(url_for("admin.submission_detail", form_id=form.id, submission_pk=submission.id))
        return render_template(
            "admin/submissions/mail.html",
            form=form,
            submission=submission,
            templates=templates,
            template_payload=mail_template_payload(templates),
            footers=footers,
        )


@bp.post("/forms/<int:form_id>/submissions/mail-selected")
@login_required
def submissions_mail_selected(form_id: int):
    selected_ids = request.form.getlist("submission_ids") or request.form.getlist("selected_submission_ids")
    selected_ids = [str(item).strip() for item in selected_ids if str(item).strip()]
    if not selected_ids:
        flash("Zaznacz co najmniej jedno zgloszenie.", "error")
        return redirect(url_for("admin.submissions_list", form_id=form_id))

    selected_numeric_ids = {int(item) for item in selected_ids if item.isdigit()}
    selected_public_ids = set(selected_ids)
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id)
        submissions = db.execute(select(FormSubmission).where(FormSubmission.form_slug == form.slug)).scalars().all()
        submissions = [
            submission
            for submission in submissions
            if submission.submission_id in selected_public_ids or submission.id in selected_numeric_ids
        ]
        templates = db.execute(
            select(MailTemplate).where(MailTemplate.form_id == form.id, MailTemplate.is_active.is_(True)).order_by(MailTemplate.name)
        ).scalars().all()
        footers = db.execute(
            select(MailFooter).where(MailFooter.form_id == form.id, MailFooter.is_active.is_(True)).order_by(MailFooter.name)
        ).scalars().all()
        summary = {"sent": 0, "failed": 0, "skipped": 0}
        for submission in submissions:
            result = send_selected_submission_mail(
                db,
                form,
                submission,
                templates,
                footers,
                trigger_event=request.form.get("trigger_event", "manual_bulk").strip() or "manual_bulk",
            )
            summary[result.status] = summary.get(result.status, 0) + 1
        db.commit()
    flash(
        f"Maile: wyslane {summary.get('sent', 0)}, pominiete {summary.get('skipped', 0)}, bledy {summary.get('failed', 0)}.",
        "success" if summary.get("sent", 0) else "error",
    )
    return redirect(url_for("admin.submissions_list", form_id=form_id))


@bp.route("/forms/<int:form_id>/mail-templates")
@login_required
def mail_templates_list(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        templates = db.execute(select(MailTemplate).where(MailTemplate.form_id == form.id).order_by(MailTemplate.name)).scalars().all()
        return render_template("admin/mail_templates/list.html", form=form, templates=templates)


@bp.get("/mail-templates")
@login_required
def mail_templates_index():
    with db_session_factory()() as db:
        forms = list_accessible_forms(db, g.admin_user)
        return render_template("admin/mail_templates/index.html", forms=forms)


@bp.route("/forms/<int:form_id>/mail-templates/new", methods=["GET", "POST"])
@bp.route("/forms/<int:form_id>/mail-templates/<int:template_id>/edit", methods=["GET", "POST"])
@login_required
def mail_template_edit(form_id: int, template_id: int | None = None):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        template = db.get(MailTemplate, template_id) if template_id else MailTemplate(form_id=form.id, name="", subject="", html_body="")
        if not template or template.form_id != form.id:
            abort(404)
        if request.method == "POST":
            uploaded_html = read_uploaded_template_file("html_file", {".html"})
            uploaded_txt = read_uploaded_template_file("txt_file", {".txt"})
            template.name = request.form.get("name", "").strip() or "Szablon"
            template.template_type = request.form.get("template_type", "").strip() or "custom"
            template.mail_type = request.form.get("mail_type", "html_text").strip() or "html_text"
            template.subject = request.form.get("subject", "").strip()
            template.content_title = request.form.get("content_title", "").strip()
            raw_html = uploaded_html if uploaded_html is not None else request.form.get("body_html", request.form.get("html_body", "")).strip()
            raw_text = uploaded_txt if uploaded_txt is not None else request.form.get("body_text", request.form.get("text_body", "")).strip()
            parsed = parse_mail_content(raw_html, raw_text) if raw_html else None
            content_html = parsed.body_html if parsed else raw_html
            content_text = raw_text or generate_text_from_html(content_html)
            template.content_html = content_html
            template.content_text = content_text
            template.html_body = content_html
            template.text_body = content_text
            template.instruction_html = request.form.get("instruction_html", "").strip()
            template.instruction_text = request.form.get("instruction_text", "").strip()
            template.footer_note = request.form.get("footer_note", "").strip()
            template.use_platform_layout = request.form.get("use_platform_layout", "on") == "on"
            if parsed and not template.content_title:
                template.content_title = parsed.title
            if parsed and not template.instruction_text and not template.instruction_html:
                template.instruction_html = parsed.instruction_html
                template.instruction_text = parsed.instruction_text
                template.footer_note = template.footer_note or parsed.footer_note
            template.trigger_event = request.form.get("trigger_event", "").strip()
            template.trigger_status = request.form.get("trigger_status", "").strip()
            template.trigger_decision = request.form.get("trigger_decision", "").strip()
            template.is_default_for_status = request.form.get("is_default_for_status") == "on"
            if template.is_default_for_status and template.trigger_status:
                for item in db.execute(
                    select(MailTemplate).where(
                        MailTemplate.form_id == form.id,
                        MailTemplate.trigger_status == template.trigger_status,
                        MailTemplate.id != (template.id or 0),
                    )
                ).scalars().all():
                    item.is_default_for_status = False
            template.is_active = request.form.get("is_active") == "on"
            db.add(template)
            db.commit()
            flash("Szablon maila zostal zapisany.", "success")
            return redirect(url_for("admin.mail_templates_list", form_id=form.id))
        sample_submissions = db.execute(
            select(FormSubmission).where(FormSubmission.form_slug == form.slug).order_by(FormSubmission.created_at.desc())
        ).scalars().all()
        preview_submission_id = parse_optional_int(request.args.get("preview_submission_id"))
        sample_submission = next((item for item in sample_submissions if item.id == preview_submission_id), None)
        if not sample_submission and sample_submissions:
            sample_submission = sample_submissions[0]
        preview_context = preview_mail_context(form, sample_submission)
        preview_html = render_platform_mail_html(template, preview_context)
        return render_template(
            "admin/mail_templates/edit.html",
            form=form,
            template=template,
            preview_html=preview_html,
            officer_decisions=OFFICER_DECISIONS,
            template_types=MAIL_TEMPLATE_TYPES,
            sample_submissions=sample_submissions,
            preview_submission_id=sample_submission.id if sample_submission else "",
            mail_layout=MAIL_LAYOUT,
        )


@bp.post("/forms/<int:form_id>/mail-templates/<int:template_id>/delete")
@login_required
def mail_template_delete(form_id: int, template_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        template = db.get(MailTemplate, template_id) or abort(404)
        if template.form_id != form.id:
            abort(404)
        db.delete(template)
        db.commit()
        flash("Szablon maila zostal usuniety.", "success")
    return redirect(url_for("admin.mail_templates_list", form_id=form_id))


@bp.route("/forms/<int:form_id>/mail-templates/import-html", methods=["GET", "POST"])
@login_required
def mail_template_import_html(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        if request.method == "GET":
            return render_template("admin/mail_templates/import_html.html", form=form, officer_decisions=OFFICER_DECISIONS, template_types=MAIL_TEMPLATE_TYPES)

        uploaded_html = read_uploaded_template_file("html_file", {".html"})
        uploaded_txt = read_uploaded_template_file("txt_file", {".txt"})
        raw_html = uploaded_html if uploaded_html is not None else request.form.get("body_html", "").strip()
        raw_text = uploaded_txt if uploaded_txt is not None else request.form.get("body_text", "").strip()
        if not raw_html:
            flash("Wgraj plik HTML albo wklej HTML.", "error")
            return render_template("admin/mail_templates/import_html.html", form=form, officer_decisions=OFFICER_DECISIONS, template_types=MAIL_TEMPLATE_TYPES), 400

        parsed = parse_mail_content(raw_html, raw_text)
        content_text = raw_text or generate_text_from_html(parsed.body_html)
        template = MailTemplate(
            form_id=form.id,
            name=request.form.get("name", "").strip() or parsed.title or "Szablon maila",
            template_type=request.form.get("template_type", "").strip() or "custom",
            mail_type="html_text",
            subject=request.form.get("subject", "").strip(),
            content_title=request.form.get("content_title", "").strip() or parsed.title,
            content_html=parsed.body_html,
            content_text=content_text,
            html_body=parsed.body_html,
            text_body=content_text,
            instruction_html=parsed.instruction_html,
            instruction_text=parsed.instruction_text,
            footer_note=parsed.footer_note,
            use_platform_layout=True,
            trigger_event=request.form.get("trigger_event", "").strip(),
            trigger_status=request.form.get("trigger_status", "").strip(),
            trigger_decision=request.form.get("trigger_decision", "").strip(),
            is_default_for_status=request.form.get("is_default_for_status") == "on",
            is_active=True,
        )
        db.add(template)
        db.flush()
        if template.is_default_for_status and template.trigger_status:
            for item in db.execute(
                select(MailTemplate).where(
                    MailTemplate.form_id == form.id,
                    MailTemplate.trigger_status == template.trigger_status,
                    MailTemplate.id != template.id,
                )
            ).scalars().all():
                item.is_default_for_status = False
        db.commit()
        flash("Szablon maila zostal zaimportowany z HTML.", "success")
        return redirect(url_for("admin.mail_template_edit", form_id=form.id, template_id=template.id))


@bp.route("/forms/<int:form_id>/mail-templates/import-zip", methods=["GET", "POST"])
@login_required
def mail_template_import_zip(form_id: int):
    if g.admin_user.role != ROLE_SUPER_ADMIN:
        abort(403)
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        if request.method == "GET":
            return render_template("admin/mail_templates/import_zip.html", form=form, officer_decisions=OFFICER_DECISIONS, template_types=MAIL_TEMPLATE_TYPES)

        uploaded_file = request.files.get("zip_file")
        if not uploaded_file or not uploaded_file.filename:
            flash("Wybierz paczke ZIP.", "error")
            return render_template("admin/mail_templates/import_zip.html", form=form, officer_decisions=OFFICER_DECISIONS, template_types=MAIL_TEMPLATE_TYPES), 400
        try:
            parsed = import_mail_template_zip(uploaded_file.read())
            template = MailTemplate(
                form_id=form.id,
                name=request.form.get("name", "").strip() or parsed.title or Path(uploaded_file.filename).stem or "Szablon ZIP",
                template_type=request.form.get("template_type", "").strip() or "custom",
                mail_type="html_text",
                subject=request.form.get("subject", "").strip(),
                content_title=request.form.get("content_title", "").strip() or parsed.title,
                content_html=parsed.body_html,
                content_text=parsed.body_text or generate_text_from_html(parsed.body_html),
                html_body=parsed.body_html,
                text_body=parsed.body_text or generate_text_from_html(parsed.body_html),
                instruction_html=parsed.instruction_html,
                instruction_text=parsed.instruction_text,
                footer_note=parsed.footer_note,
                use_platform_layout=True,
                trigger_event=request.form.get("trigger_event", "").strip(),
                trigger_status=request.form.get("trigger_status", "").strip(),
                trigger_decision=request.form.get("trigger_decision", "").strip(),
                is_default_for_status=request.form.get("is_default_for_status") == "on",
                is_active=True,
            )
            db.add(template)
            db.flush()
            for asset in parsed.assets:
                db.add(MailTemplateAsset(template_id=template.id, **asset))
            if template.is_default_for_status and template.trigger_status:
                for item in db.execute(
                    select(MailTemplate).where(
                        MailTemplate.form_id == form.id,
                        MailTemplate.trigger_status == template.trigger_status,
                        MailTemplate.id != template.id,
                    )
                ).scalars().all():
                    item.is_default_for_status = False
            db.commit()
        except MailImportError as exc:
            flash(str(exc), "error")
            return render_template("admin/mail_templates/import_zip.html", form=form, officer_decisions=OFFICER_DECISIONS, template_types=MAIL_TEMPLATE_TYPES), 400

        flash("Szablon maila zostal zaimportowany z ZIP.", "success")
        return redirect(url_for("admin.mail_template_edit", form_id=form.id, template_id=template.id))


@bp.route("/forms/<int:form_id>/mail-footers")
@login_required
def mail_footers_list(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        footers = db.execute(select(MailFooter).where(MailFooter.form_id == form.id).order_by(MailFooter.name)).scalars().all()
        return render_template("admin/mail_footers/list.html", form=form, footers=footers)


@bp.route("/forms/<int:form_id>/mail-footers/new", methods=["GET", "POST"])
@bp.route("/forms/<int:form_id>/mail-footers/<int:footer_id>/edit", methods=["GET", "POST"])
@login_required
def mail_footer_edit(form_id: int, footer_id: int | None = None):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        footer = db.get(MailFooter, footer_id) if footer_id else MailFooter(form_id=form.id, name="", html_body="")
        if not footer or footer.form_id != form.id:
            abort(404)
        logos = list_active_logos(db)
        if request.method == "POST":
            footer.name = request.form.get("name", "").strip() or "Stopka"
            footer.html_body = request.form.get("html_body", "").strip()
            selected_logo_id = parse_optional_int(request.form.get("logo_id"))
            from . import can_select_active_logo

            if selected_logo_id and not can_select_active_logo(db, selected_logo_id):
                abort(403)
            footer.logo_id = selected_logo_id
            footer.is_active = request.form.get("is_active") == "on"
            footer.is_default = request.form.get("is_default") == "on"
            if footer.is_default:
                for item in db.execute(select(MailFooter).where(MailFooter.form_id == form.id)).scalars().all():
                    item.is_default = False
            db.add(footer)
            db.commit()
            flash("Stopka maila zostala zapisana.", "success")
            return redirect(url_for("admin.mail_footers_list", form_id=form.id))
        return render_template("admin/mail_footers/edit.html", form=form, footer=footer, logos=logos)


def send_admin_mail(db, form, submission, templates: list[MailTemplate], footers: list[MailFooter]):
    template_id = int(request.form.get("template_id") or 0)
    footer_id = int(request.form.get("footer_id") or 0)
    template = next((item for item in templates if item.id == template_id), None)
    if templates and template is None:
        template = templates[0]
    footer = next((item for item in footers if item.id == footer_id), None)
    fallback = template or system_mail_template(form, submission)
    subject_template = request.form.get("subject", "").strip() or getattr(fallback, "subject", "")
    body_template = request.form.get("body_html", request.form.get("html_body", "")).strip() or template_body_html(fallback)
    text_template = request.form.get("body_text", request.form.get("text_body", "")).strip() or template_body_text(fallback) or body_template
    render_template_obj = SimpleNamespace(
        id=getattr(template, "id", None),
        name=getattr(fallback, "name", "") or subject_template or "Mail",
        subject=subject_template,
        content_title=getattr(fallback, "content_title", "") or subject_template,
        html_body=body_template,
        text_body=text_template,
        instruction_html=getattr(fallback, "instruction_html", ""),
        instruction_text=getattr(fallback, "instruction_text", ""),
        footer_note=getattr(fallback, "footer_note", ""),
        use_platform_layout=getattr(fallback, "use_platform_layout", True),
    )
    return send_mail_for_submission(
        db,
        form,
        submission,
        template=render_template_obj,
        log_template=template,
        footer=footer,
        to_email=request.form.get("to_email", "").strip() or submission.email,
        subject_template=subject_template,
        event_type="manual",
    )


def send_selected_submission_mail(db, form, submission, templates: list[MailTemplate], footers: list[MailFooter], *, trigger_event: str):
    template = select_mail_template(templates, submission, trigger_event) or (templates[0] if templates else None)
    footer = next((item for item in footers if item.is_default), None)
    if not template:
        template = system_mail_template(form, submission)
    return send_mail_for_submission(
        db,
        form,
        submission,
        template=template,
        log_template=template if getattr(template, "id", None) else None,
        footer=footer,
        to_email=submission.email,
        subject_template=template.subject,
        event_type=trigger_event,
    )


def select_mail_template(templates: list[MailTemplate], submission: FormSubmission, trigger_event: str) -> MailTemplate | None:
    return current_app.extensions["services"].mail_dispatch_service.select_template(templates, submission, trigger_event)


def mail_template_payload(templates: list[MailTemplate]) -> list[dict]:
    return [
        {
            "id": str(template.id),
            "subject": template.subject or "",
            "html_body": template_body_html(template),
            "text_body": template_body_text(template),
        }
        for template in templates
    ]


def template_body_html(template) -> str:
    return (
        getattr(template, "content_html", "")
        or getattr(template, "html_body", "")
        or getattr(template, "body_html", "")
        or getattr(template, "content_intro", "")
        or ""
    )


def template_body_text(template) -> str:
    return (
        getattr(template, "content_text", "")
        or getattr(template, "text_body", "")
        or getattr(template, "body_text", "")
        or ""
    )


def system_mail_template(form, submission):
    return SimpleNamespace(
        id=None,
        name="Mail systemowy",
        subject="Informacja dotyczaca zgloszenia {{ submission_id }}",
        content_title="Informacja dotyczaca zgloszenia",
        html_body=(
            "<p>Dzien dobry,</p>"
            "<p>Przesylamy informacje dotyczaca zgloszenia "
            "<strong>{{ submission_id }}</strong> w formularzu <strong>{{ form_name }}</strong>.</p>"
        ),
        text_body=(
            "Dzien dobry,\n\n"
            "Przesylamy informacje dotyczaca zgloszenia {{ submission_id }} "
            "w formularzu {{ form_name }}."
        ),
        instruction_html="",
        instruction_text="",
        footer_note="Pozdrawiamy",
        use_platform_layout=True,
    )


def send_mail_for_submission(
    db,
    form,
    submission,
    *,
    template,
    footer,
    to_email: str,
    subject_template: str,
    event_type: str,
    log_template=None,
):
    files = current_app.extensions["services"].submission_document_service.list_documents(submission.submission_id)
    service = current_app.extensions["services"].mail_dispatch_service
    return service.dispatch_to_submission(
        db=db,
        form=form,
        submission=submission,
        template=template,
        footer=footer,
        to_email=to_email,
        subject_template=subject_template,
        event_type=event_type,
        sent_by_id=g.admin_user.id,
        files=files,
        context_builders={
            "documents_to_sign_url_builder": lambda item: url_for("documents.documents_to_sign", submission_id=item.submission_id, _external=True),
            "document_url_builder": lambda item, filename: current_app.extensions["services"].document_service.build_download_url(
                {"form_slug": item.form_slug, "submission_id": item.submission_id, "access_token": item.access_token},
                filename,
            ),
        },
        logo_url_builder=lambda logo: url_for("public_forms.logo_asset", logo_id=logo.id, filename=logo.filename, _external=True),
    )


def mail_flash(result):
    if result.status == "sent":
        recipient = f" do {result.recipient}" if result.recipient else ""
        return f"Mail wyslany{recipient}.", "success"
    reason = result.error_message or "Brak szczegolow bledu."
    if result.status == "skipped":
        return f"Mail nie zostal wyslany: {reason}", "error"
    return f"Nie udalo sie wyslac maila: {reason}", "error"
