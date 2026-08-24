from __future__ import annotations

from pathlib import Path
import re
from types import SimpleNamespace

from flask import abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy import or_, select

from models import Form, FormSubmission, MailFooter, MailTemplate, MailTemplateAsset
from services.instruction_html_service import sanitize_instruction_html
from services.mail_template_service import (
    MAIL_LAYOUT,
    MailImportError,
    generate_text_from_html,
    import_mail_template_zip,
    parse_mail_content,
    render_platform_mail_html,
    sanitize_content_html,
    render_platform_mail_text,
    render_template_text,
)
from services.mail_template_editor_service import (
    build_variable_catalog,
)
from services.workflow_mail_trigger_service import WorkflowMailTriggerService

from . import (
    MAIL_TEMPLATE_TYPES,
    ROLE_SUPER_ADMIN,
    bp,
    can_manage_form,
    db_session_factory,
    ensure_form_access,
    list_accessible_forms,
    login_required,
    list_active_logos,
    parse_optional_int,
    permission_required,
    preview_mail_context,
    read_uploaded_template_file,
)


MAIL_TEMPLATE_LABELS = {
    "correction_accepted": "Akceptacja poprawionego wniosku",
    "confirmation": "Złożenie wniosku",
    "accepted": "Akceptacja",
    "rejected": "Odrzucenie",
    "auto_rejected_by_condition": "Automatyczne odrzucenie",
    "correction_required": "Wysłanie do poprawy",
    "returned_for_correction": "Wysłanie do poprawy",
    "declaration_ready": "Deklaracja gotowa",
    "declaration_signed": "Deklaracja podpisana",
    "agreement_ready": "Umowa gotowa",
    "agreement_signed_by_user": "Umowa podpisana przez beneficjenta",
    "agreement_signed_by_office": "Umowa podpisana przez urząd",
    "stage_rollback": "Cofnięcie etapu",
    "repeatable_item_decision": "Decyzja dla elementu listy",
    "custom": "Wiadomość własna",
}
MAIL_PLACEHOLDER_PATTERN = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)")


@bp.route("/forms/<int:form_id>/submissions/<int:submission_pk>/mail", methods=["GET", "POST"])
@login_required
def submission_mail(form_id: int, submission_pk: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, permission="can_send_email")
        submission = db.get(FormSubmission, submission_pk) or abort(404)
        if submission.form_slug != form.slug:
            abort(404)
        can_view_sensitive_data = current_app.extensions["services"].permission_service.has_permission(
            db, g.admin_user, "can_view_sensitive_data", form=form
        )
        templates = db.execute(
            select(MailTemplate).where(MailTemplate.form_id == form.id, MailTemplate.is_active.is_(True)).order_by(MailTemplate.name)
        ).scalars().all()
        footers = db.execute(
            select(MailFooter)
            .where(or_(MailFooter.form_id == form.id, MailFooter.form_id.is_(None)), MailFooter.is_active.is_(True))
            .order_by(MailFooter.form_id.desc(), MailFooter.name)
        ).scalars().all()
        if request.method == "POST":
            result = send_admin_mail(db, form, submission, templates, footers)
            db.commit()
            flash(*mail_flash(result))
            return redirect(url_for("admin.submission_detail", form_id=form.id, submission_pk=submission.id))
        resolved_footer = current_app.extensions["services"].mail_dispatch_service.mail_footer_resolver.resolve(
            db,
            mail_type="manual",
            form=form,
            submission=submission,
        )
        return render_template(
            "admin/submissions/mail.html",
            form=form,
            submission=submission,
            templates=templates,
            template_payload=mail_template_payload(templates),
            footers=footers,
            footer_usage_label=(
                "Używana jest stopka formularza"
                if resolved_footer and resolved_footer.form_id == form.id
                else "Używana jest stopka ogólna"
                if resolved_footer
                else "Brak aktywnej stopki e-mail"
            ),
            variable_catalog=build_variable_catalog(
                form,
                _safe_preview_context(db, form, submission),
            ),
            can_view_sensitive_data=can_view_sensitive_data,
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
            select(MailFooter)
            .where(or_(MailFooter.form_id == form.id, MailFooter.form_id.is_(None)), MailFooter.is_active.is_(True))
            .order_by(MailFooter.form_id.desc(), MailFooter.name)
        ).scalars().all()
        if request.form.get("compose") == "1" and request.form.get("send_now") != "1":
            preview_submission = submissions[0] if submissions else None
            return render_template(
                "admin/submissions/mail_bulk.html",
                form=form,
                submissions=submissions,
                templates=templates,
                template_payload=mail_template_payload(templates),
                variable_catalog=build_variable_catalog(form, _safe_preview_context(db, form, preview_submission)),
                missing_email_submissions=[item for item in submissions if not str(item.email or "").strip()],
            )
        manual_template = _manual_template_from_request()
        summary = {"sent": 0, "failed": 0, "skipped": 0}
        skipped_ids = []
        for submission in submissions:
            result = send_selected_submission_mail(
                db,
                form,
                submission,
                templates,
                footers,
                trigger_event=request.form.get("trigger_event", "manual_bulk").strip() or "manual_bulk",
                manual_template=manual_template,
            )
            summary[result.status] = summary.get(result.status, 0) + 1
            if result.status == "skipped":
                skipped_ids.append(submission.submission_id)
        db.commit()
    flash(
        f"Wiadomości: wysłane {summary.get('sent', 0)}, pominięte {summary.get('skipped', 0)}, błędy {summary.get('failed', 0)}."
        + (f" Pominięte zgłoszenia: {', '.join(skipped_ids)}." if skipped_ids else ""),
        "success" if summary.get("sent", 0) else "error",
    )
    return redirect(url_for("admin.submissions_list", form_id=form_id))


@bp.route("/forms/<int:form_id>/mail-templates")
@login_required
def mail_templates_list(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id)
        templates = db.execute(select(MailTemplate).where(MailTemplate.form_id == form.id).order_by(MailTemplate.name)).scalars().all()
        return render_template(
            "admin/mail_templates/list.html",
            form=form,
            templates=templates,
            read_only=not can_manage_form(db, g.admin_user, form.id),
            template_labels=MAIL_TEMPLATE_LABELS,
        )


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
        template = db.get(MailTemplate, template_id) if template_id else MailTemplate(
            form_id=form.id, name="", subject="", html_body="", show_process_status=True
        )
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
            template.show_process_status = request.form.get("show_process_status") == "on"
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
        preview_context = _safe_preview_context(db, form, sample_submission)
        preview_html = render_platform_mail_html(template, preview_context)
        variable_catalog = build_variable_catalog(form, preview_context)
        trigger_catalog = WorkflowMailTriggerService().options_for_form(form)
        return render_template(
            "admin/mail_templates/edit.html",
            form=form,
            template=template,
            preview_html=preview_html,
            template_types=MAIL_TEMPLATE_TYPES,
            sample_submissions=sample_submissions,
            preview_submission_id=sample_submission.id if sample_submission else "",
            mail_layout=MAIL_LAYOUT,
            template_labels=MAIL_TEMPLATE_LABELS,
            variable_catalog=variable_catalog,
            trigger_catalog=trigger_catalog,
            trigger_events=trigger_catalog["events"],
            trigger_statuses=trigger_catalog["statuses"],
            trigger_decisions=trigger_catalog["decisions"],
            preview_subject=render_template_text(template.subject or "", preview_context),
            preview_text=render_platform_mail_text(template, preview_context),
        )


@bp.post("/forms/<int:form_id>/mail-templates/preview")
@login_required
def mail_template_preview(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        sample_submission = None
        submission_id = parse_optional_int(request.form.get("preview_submission_id"))
        if submission_id:
            candidate = db.get(FormSubmission, submission_id)
            if candidate and candidate.form_slug == form.slug:
                sample_submission = candidate
        context = _safe_preview_context(db, form, sample_submission)
        trigger_catalog = WorkflowMailTriggerService().options_for_form(form)
        selected_event = request.form.get("trigger_event", "").strip()
        selected_status = request.form.get("trigger_status", "").strip()
        selected_decision = request.form.get("trigger_decision", "").strip()
        context.update(
            {
                "trigger_event": selected_event,
                "trigger_status": selected_status,
                "trigger_decision": selected_decision,
            }
        )
        if selected_status:
            status_option = next(
                (item for item in trigger_catalog["statuses"] if item["value"] == selected_status),
                None,
            )
            context["process_status"] = selected_status
            context["process_status_label"] = (
                status_option["label"] if status_option else selected_status
            )
            context["status_label"] = context["process_status_label"]
        if selected_decision:
            context["officer_decision"] = selected_decision
        body_html = sanitize_content_html(request.form.get("body_html", ""))
        instruction_html = sanitize_content_html(request.form.get("instruction_html", ""))
        template = SimpleNamespace(
            name=request.form.get("name", "").strip() or "Wiadomość",
            content_title=request.form.get("content_title", "").strip(),
            content_html=body_html,
            html_body=body_html,
            content_text=request.form.get("body_text", ""),
            text_body=request.form.get("body_text", ""),
            instruction_html=instruction_html,
            instruction_text=request.form.get("instruction_text", ""),
            footer_note=request.form.get("footer_note", ""),
        )
        try:
            source_values = [
                request.form.get("subject", ""),
                request.form.get("content_title", ""),
                request.form.get("body_html", ""),
                request.form.get("body_text", ""),
                request.form.get("instruction_html", ""),
                request.form.get("instruction_text", ""),
                request.form.get("footer_note", ""),
            ]
            unknown_variables = sorted(
                {
                    name
                    for source in source_values
                    for name in MAIL_PLACEHOLDER_PATTERN.findall(source or "")
                    if name not in context
                }
            )
            return jsonify(
                {
                    "ok": True,
                    "subject": render_template_text(request.form.get("subject", ""), context),
                    "title": render_template_text(request.form.get("content_title", ""), context),
                    "html": render_platform_mail_html(template, context),
                    "text": render_platform_mail_text(template, context),
                    "unknown_variables": unknown_variables,
                    "warnings": [
                        f"Nierozpoznana zmienna: {{{{ {name} }}}}" for name in unknown_variables
                    ],
                }
            )
        except Exception:
            current_app.logger.exception("mail_template_preview_failed form_id=%s", form_id)
            return jsonify({"ok": False, "error": "Nie udało się wyrenderować podglądu. Sprawdź składnię zmiennych."}), 400


@bp.get("/forms/<int:form_id>/mail-variables")
@login_required
def form_mail_variables(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, permission="can_send_email")
        submission = None
        submission_pk = parse_optional_int(request.args.get("submission_id"))
        if submission_pk:
            candidate = db.get(FormSubmission, submission_pk)
            if candidate and candidate.form_slug == form.slug:
                submission = candidate
        return jsonify(_mail_variable_payload(form, submission, db=db))


@bp.get("/submissions/<int:submission_pk>/mail-variables")
@login_required
def submission_mail_variables(submission_pk: int):
    with db_session_factory()() as db:
        submission = db.get(FormSubmission, submission_pk) or abort(404)
        form = db.execute(select(Form).where(Form.slug == submission.form_slug)).scalar_one_or_none() or abort(404)
        ensure_form_access(db, form.id)
        return jsonify(_mail_variable_payload(form, submission, db=db))


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
            return render_template(
                "admin/mail_templates/import_html.html",
                form=form,
                template_types=MAIL_TEMPLATE_TYPES,
                **_mail_trigger_template_context(form),
            )

        uploaded_html = read_uploaded_template_file("html_file", {".html"})
        uploaded_txt = read_uploaded_template_file("txt_file", {".txt"})
        raw_html = uploaded_html if uploaded_html is not None else request.form.get("body_html", "").strip()
        raw_text = uploaded_txt if uploaded_txt is not None else request.form.get("body_text", "").strip()
        if not raw_html:
            flash("Wgraj plik HTML albo wklej HTML.", "error")
            return render_template(
                "admin/mail_templates/import_html.html",
                form=form,
                template_types=MAIL_TEMPLATE_TYPES,
                **_mail_trigger_template_context(form),
            ), 400

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
            return render_template(
                "admin/mail_templates/import_zip.html",
                form=form,
                template_types=MAIL_TEMPLATE_TYPES,
                **_mail_trigger_template_context(form),
            )

        uploaded_file = request.files.get("zip_file")
        if not uploaded_file or not uploaded_file.filename:
            flash("Wybierz paczke ZIP.", "error")
            return render_template(
                "admin/mail_templates/import_zip.html",
                form=form,
                template_types=MAIL_TEMPLATE_TYPES,
                **_mail_trigger_template_context(form),
            ), 400
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
            return render_template(
                "admin/mail_templates/import_zip.html",
                form=form,
                template_types=MAIL_TEMPLATE_TYPES,
                **_mail_trigger_template_context(form),
            ), 400

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
        footer = db.get(MailFooter, footer_id) if footer_id else db.execute(
            select(MailFooter).where(MailFooter.form_id == form.id).order_by(MailFooter.is_default.desc(), MailFooter.id)
        ).scalars().first()
        footer = footer or MailFooter(form_id=form.id, name="Stopka formularza", html_body="")
        if not footer or footer.form_id != form.id:
            abort(404)
        logos = list_active_logos(db)
        if request.method == "POST":
            try:
                _update_mail_footer_from_form(footer)
            except ValueError as exc:
                flash(str(exc), "error")
                return render_template(
                    "admin/mail_footers/edit.html",
                    form=form,
                    footer=footer,
                    logos=logos,
                    is_global=False,
                    global_footer=_global_mail_footer(db),
                    footer_usage_label="Używana jest stopka formularza",
                ), 400
            selected_logo_id = parse_optional_int(request.form.get("logo_id"))
            from . import can_select_active_logo

            if selected_logo_id and not can_select_active_logo(db, selected_logo_id):
                abort(403)
            footer.logo_id = selected_logo_id
            footer.logo_path = ""
            footer.is_default = True
            if footer.is_default:
                for item in db.execute(select(MailFooter).where(MailFooter.form_id == form.id)).scalars().all():
                    item.is_default = False
            db.add(footer)
            db.commit()
            flash("Stopka maila zostala zapisana.", "success")
            return redirect(url_for("admin.mail_footers_list", form_id=form.id))
        global_footer = _global_mail_footer(db)
        return render_template(
            "admin/mail_footers/edit.html",
            form=form,
            footer=footer,
            logos=logos,
            is_global=False,
            global_footer=global_footer,
            footer_usage_label=(
                "Używana jest stopka ogólna"
                if footer.use_global or (footer.id is not None and not footer.is_active)
                else "Używana jest stopka formularza"
            ),
        )


@bp.route("/mail-footer", methods=["GET", "POST"])
@permission_required("can_manage_site")
def global_mail_footer_edit():
    with db_session_factory()() as db:
        footer = _global_mail_footer(db) or MailFooter(form_id=None, name="Stopka ogólna", html_body="", is_default=True)
        logos = list_active_logos(db)
        if request.method == "POST":
            try:
                _update_mail_footer_from_form(footer)
            except ValueError as exc:
                flash(str(exc), "error")
                return render_template(
                    "admin/mail_footers/edit.html",
                    form=None,
                    footer=footer,
                    logos=logos,
                    is_global=True,
                    global_footer=footer,
                    footer_usage_label="Używana jest stopka ogólna",
                ), 400
            footer.form_id = None
            footer.is_default = True
            footer.use_global = False
            selected_logo_id = parse_optional_int(request.form.get("logo_id"))
            from . import can_select_active_logo

            if selected_logo_id and not can_select_active_logo(db, selected_logo_id):
                abort(403)
            footer.logo_id = selected_logo_id
            footer.logo_path = ""
            for item in db.execute(select(MailFooter).where(MailFooter.form_id.is_(None))).scalars().all():
                item.is_default = False
            db.add(footer)
            db.commit()
            flash("Stopka ogólna została zapisana.", "success")
            return redirect(url_for("admin.global_mail_footer_edit"))
        return render_template(
            "admin/mail_footers/edit.html",
            form=None,
            footer=footer,
            logos=logos,
            is_global=True,
            global_footer=footer,
            footer_usage_label="Używana jest stopka ogólna",
        )


def _global_mail_footer(db) -> MailFooter | None:
    return db.execute(
        select(MailFooter).where(MailFooter.form_id.is_(None)).order_by(MailFooter.is_default.desc(), MailFooter.id)
    ).scalars().first()


def _update_mail_footer_from_form(footer: MailFooter) -> None:
    alignment = str(request.form.get("logo_alignment") or "left").strip()
    if alignment not in {"left", "center", "right"}:
        raise ValueError("Wybierz prawidłowe wyrównanie logo.")
    position = str(request.form.get("logo_position") or "top").strip()
    if position not in {"top", "bottom", "left", "right", "inline"}:
        raise ValueError("Wybierz prawidłowe położenie logo.")
    width = _parse_footer_logo_dimension(
        request.form.get("logo_width"),
        minimum=20,
        maximum=800,
        label="Szerokość logo",
    )
    height = _parse_footer_logo_dimension(
        request.form.get("logo_height"),
        minimum=20,
        maximum=400,
        label="Wysokość logo",
    )

    footer.name = request.form.get("name", "").strip() or ("Stopka ogólna" if footer.form_id is None else "Stopka formularza")
    footer.html_body = sanitize_instruction_html(request.form.get("html_body", ""))
    footer.contact_html = sanitize_instruction_html(request.form.get("contact_html", ""))
    footer.legal_text = sanitize_instruction_html(request.form.get("legal_text", ""))
    footer.logo_alignment = alignment
    footer.logo_position = position
    footer.logo_width = width
    footer.logo_height = height
    footer.links = _parse_footer_links(request.form.get("links_text", ""))
    footer.is_active = request.form.get("is_active") == "on"
    footer.use_global = request.form.get("use_global") == "on"


def _parse_footer_logo_dimension(value: str | None, *, minimum: int, maximum: int, label: str) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise ValueError(f"{label} musi być liczbą całkowitą od {minimum} do {maximum} px.") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{label} musi mieć wartość od {minimum} do {maximum} px.")
    return parsed


def _parse_footer_links(value: str) -> list[dict[str, str]]:
    links = []
    for raw_line in str(value or "").splitlines()[:20]:
        label, separator, url = raw_line.partition("|")
        url = url.strip()
        if not separator or not label.strip() or not url.lower().startswith(("https://", "http://", "mailto:", "tel:")):
            continue
        links.append({"label": label.strip()[:120], "url": url[:1000]})
    return links


def send_admin_mail(db, form, submission, templates: list[MailTemplate], footers: list[MailFooter]):
    template_id = int(request.form.get("template_id") or 0)
    footer_id = int(request.form.get("footer_id") or 0)
    template = _manual_template_from_request() or next((item for item in templates if item.id == template_id), None)
    if templates and template is None:
        template = templates[0]
    footer = next((item for item in footers if item.id == footer_id), None) if footer_id else select_default_footer(footers, form_id=form.id)
    return send_mail_for_submission(
        db,
        form,
        submission,
        template=template,
        log_template=template,
        footer=footer,
        to_email=request.form.get("to_email", "").strip() or submission.email,
        subject_template=getattr(template, "subject", ""),
        event_type="manual",
    )


def send_selected_submission_mail(
    db,
    form,
    submission,
    templates: list[MailTemplate],
    footers: list[MailFooter],
    *,
    trigger_event: str,
    manual_template=None,
):
    template = manual_template or select_mail_template(templates, submission, trigger_event) or (templates[0] if templates else None)
    footer = select_default_footer(footers, form_id=form.id)
    return send_mail_for_submission(
        db,
        form,
        submission,
        template=template,
        log_template=template,
        footer=footer,
        to_email=submission.email,
        subject_template=getattr(template, "subject", ""),
        event_type=trigger_event,
    )


def select_mail_template(templates: list[MailTemplate], submission: FormSubmission, trigger_event: str) -> MailTemplate | None:
    return current_app.extensions["services"].mail_dispatch_service.select_template(templates, submission, trigger_event)


def select_default_footer(footers: list[MailFooter], *, form_id: int) -> MailFooter | None:
    form_footer = next(
        (item for item in footers if item.form_id == form_id and item.is_default and item.is_active and not item.use_global),
        None,
    )
    if form_footer:
        current_app.logger.info("mail_footer_selected scope=form footer_id=%s form_id=%s", form_footer.id, form_id)
        return form_footer
    global_footer = next((item for item in footers if item.form_id is None and item.is_default and item.is_active), None)
    if global_footer:
        current_app.logger.info("mail_footer_selected scope=global footer_id=%s form_id=%s", global_footer.id, form_id)
        return global_footer
    current_app.logger.info("mail_footer_selected scope=none form_id=%s", form_id)
    return None


def _manual_template_from_request():
    subject = str(request.form.get("subject") or "").strip()
    html_body = str(request.form.get("html_body") or "").strip()
    text_body = str(request.form.get("text_body") or "").strip()
    if not any((subject, html_body, text_body)):
        return None
    return SimpleNamespace(
        id=None,
        subject=subject,
        content_title="Wiadomość",
        content_html=sanitize_content_html(html_body),
        content_text=text_body,
        html_body=sanitize_content_html(html_body),
        text_body=text_body,
        instruction_html="",
        instruction_text="",
        footer_note="",
        use_platform_layout=True,
        is_active=True,
    )


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


def _safe_preview_context(db, form, submission=None) -> dict:
    context = preview_mail_context(form, submission)
    for key in ("access_token", "podpisz_url", "pobierz_url", "document_url", "signed_agreement_download_link"):
        if key in context:
            context[key] = "Dane ukryte — sekret techniczny"
        nested = context.get("submission")
        if isinstance(nested, dict) and key in nested:
            nested[key] = "Dane ukryte — sekret techniczny"
    if submission is None or current_app.extensions["services"].permission_service.has_permission(
        db, g.admin_user, "can_view_sensitive_data", form=form
    ):
        return context
    from models import FormVersion
    from services.admin_submission_service import BUILTIN_SENSITIVE_FIELDS
    definition = form.definition_json or {}
    if submission is not None and submission.form_version_id:
        version = db.get(FormVersion, submission.form_version_id)
        if version:
            definition = version.definition_json or definition
    classified = {
        str(field.get("name") or field.get("key") or "")
        for field in definition.get("fields", []) if isinstance(field, dict)
        and str(field.get("data_classification") or field.get("sensitivity") or "normal") != "normal"
    }
    for key in BUILTIN_SENSITIVE_FIELDS | classified:
        if key in context:
            context[key] = "Dane ukryte — brak uprawnienia"
        for nested_key in ("submission", "data_json"):
            nested = context.get(nested_key)
            if isinstance(nested, dict) and key in nested:
                nested[key] = "Dane ukryte — brak uprawnienia"
    return context


def _mail_variable_payload(form, submission=None, *, db=None) -> dict:
    context = _safe_preview_context(db, form, submission) if db is not None else preview_mail_context(form, submission)
    groups = build_variable_catalog(form, context)
    return {
        "form_id": form.id,
        "submission_id": submission.id if submission else None,
        "categories": [
            {
                "name": group["category"],
                "variables": group["variables"],
            }
            for group in groups
        ],
    }


def _mail_trigger_template_context(form) -> dict:
    catalog = WorkflowMailTriggerService().options_for_form(form)
    return {
        "trigger_catalog": catalog,
        "trigger_events": catalog["events"],
        "trigger_statuses": catalog["statuses"],
        "trigger_decisions": catalog["decisions"],
    }


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
