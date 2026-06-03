from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import secrets
import zipfile
from datetime import datetime, timezone
from functools import wraps
from html import escape
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from xml.etree import ElementTree

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    render_template_string,
    request,
    send_file,
    session,
    url_for,
)
from sqlalchemy import func, select
from werkzeug.security import check_password_hash, generate_password_hash

from database import create_session_factory
from form_loader import normalize_form_definition, validate_form_definition
from models import (
    EmailLog,
    Form,
    FormField,
    FormPermission,
    FormSubmission,
    MailFooter,
    MailTemplate,
    MailTemplateAsset,
    Logo,
    SubmissionFile,
    User,
)
from services.mail_template_service import (
    MAIL_LAYOUT,
    MailImportError,
    build_mail_context as build_platform_mail_context,
    generate_text_from_html,
    build_instruction_html,
    import_mail_template_zip,
    parse_mail_content,
    render_platform_mail_html,
    render_platform_mail_text,
    render_template_text,
)


bp = Blueprint("admin", __name__, url_prefix="/admin")

ROLE_SUPER_ADMIN = "super_admin"
ROLE_ADMIN = "admin"
ROLE_FORM_MANAGER = "form_manager"
ROLES = {ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_FORM_MANAGER}
TECHNICAL_SORT_FIELDS = {"created_at", "process_status", "officer_decision", "email", "nazwisko", "submission_id"}
PLACEHOLDER_PATTERN = re.compile(r"{{\s*([a-zA-Z0-9_]+)\s*}}")
FIELD_TYPES = ["text", "textarea", "email", "tel", "number", "date", "select", "radio", "checkbox", "pesel"]
OFFICER_DECISIONS = [
    ("", "Brak decyzji"),
    ("accepted", "Zaakceptowano"),
    ("rejected", "Odrzucono"),
    ("correction", "Do poprawy"),
]
LOGO_MIME_TYPES = {"image/png", "image/jpeg", "image/svg+xml", "image/webp", "image/gif"}
MAIL_TEMPLATE_TYPES = [
    "confirmation",
    "accepted",
    "rejected",
    "correction_required",
    "declaration_signed",
    "agreement_ready",
    "agreement_signed_by_user",
    "agreement_signed_by_office",
    "custom",
]


def db_session_factory():
    database_url = current_app.config.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for /admin.")
    return create_session_factory(database_url)


def csrf_token() -> str:
    token = session.get("admin_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["admin_csrf_token"] = token
    return token


def validate_csrf() -> None:
    if request.method != "POST":
        return
    expected = session.get("admin_csrf_token")
    provided = request.form.get("csrf_token")
    if not expected or not provided or not secrets.compare_digest(expected, provided):
        abort(400)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_user_id"):
            return redirect(url_for("admin.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def role_required(*roles: str):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = get_current_user()
            if not user or user.role not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def get_current_user() -> User | None:
    user_id = session.get("admin_user_id")
    if not user_id:
        return None
    with db_session_factory()() as db:
        user = db.get(User, int(user_id))
        if not user or user.is_blocked or not user.is_active:
            session.pop("admin_user_id", None)
            return None
        return user


@bp.before_request
def load_current_user():
    g.admin_user = get_current_user()
    if request.method == "POST":
        validate_csrf()


@bp.app_context_processor
def inject_admin_helpers():
    return {"admin_csrf_token": csrf_token, "admin_is_active": admin_is_active}


def admin_is_active(*endpoints: str) -> bool:
    return request.endpoint in endpoints


@bp.get("/")
def admin_index():
    if session.get("admin_user_id"):
        return redirect(url_for("admin.dashboard"))
    return render_template("admin/login.html")


@bp.post("/")
def login():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    with db_session_factory()() as db:
        user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if not user or user.is_blocked or not user.is_active or not check_password_hash(user.password_hash, password):
            flash("Nieprawidlowy login lub haslo.", "error")
            return render_template("admin/login.html", email=email), 401
        session["admin_user_id"] = user.id
    return redirect(request.args.get("next") or url_for("admin.dashboard"))


@bp.get("/logout")
@login_required
def logout():
    session.pop("admin_user_id", None)
    flash("Wylogowano.", "success")
    return redirect(url_for("admin.admin_index"))


@bp.get("/dashboard")
@login_required
def dashboard():
    user = g.admin_user
    with db_session_factory()() as db:
        form_ids = accessible_form_ids(db, user)
        forms_count = count_accessible_forms(db, user, form_ids)
        submissions_query = select(func.count(FormSubmission.id))
        if user.role != ROLE_SUPER_ADMIN:
            slugs = accessible_form_slugs(db, form_ids)
            submissions_query = submissions_query.where(FormSubmission.form_slug.in_(slugs or [""]))
        submissions_count = db.execute(submissions_query).scalar() or 0
        pending_query = select(func.count(FormSubmission.id)).where(
            FormSubmission.process_status.in_(["FORM_SUBMITTED", "WAITING_FOR_OFFICER_DECISION"])
        )
        if user.role != ROLE_SUPER_ADMIN:
            slugs = accessible_form_slugs(db, form_ids)
            pending_query = pending_query.where(FormSubmission.form_slug.in_(slugs or [""]))
        pending_count = db.execute(pending_query).scalar() or 0
        documents_count = db.execute(select(func.count(SubmissionFile.id))).scalar() or 0
        email_errors_count = db.execute(select(func.count(EmailLog.id)).where(EmailLog.status == "failed")).scalar() or 0
    return render_template(
        "admin/dashboard.html",
        forms_count=forms_count,
        submissions_count=submissions_count,
        pending_count=pending_count,
        documents_count=documents_count,
        email_errors_count=email_errors_count,
    )


@bp.get("/submissions")
@login_required
def submissions_all():
    user = g.admin_user
    with db_session_factory()() as db:
        forms = list_accessible_forms(db, user)
        form_by_slug = {form.slug: form for form in forms}
        slugs = list(form_by_slug.keys())
        if user.role != ROLE_SUPER_ADMIN and not slugs:
            submissions = []
        else:
            query = select(FormSubmission)
            if user.role != ROLE_SUPER_ADMIN:
                query = query.where(FormSubmission.form_slug.in_(slugs))
            submissions = db.execute(query).scalars().all()
        submissions = filter_submissions(submissions, request.args)
        submissions = sort_submissions(
            submissions,
            request.args.get("sort") or "created_at",
            request.args.get("direction") or "desc",
        )
        return render_template(
            "admin/submissions/all.html",
            submissions=submissions,
            form_by_slug=form_by_slug,
            filters=request.args,
        )


@bp.get("/forms")
@login_required
def forms_list():
    user = g.admin_user
    with db_session_factory()() as db:
        forms = list_accessible_forms(db, user)
        counts = {
            slug: count
            for slug, count in db.execute(
                select(FormSubmission.form_slug, func.count(FormSubmission.id)).group_by(FormSubmission.form_slug)
            ).all()
        }
        return render_template("admin/forms/list.html", forms=forms, submission_counts=counts)


@bp.route("/forms/upload", methods=["GET", "POST"])
@login_required
def forms_upload():
    if request.method == "GET":
        return render_template("admin/forms/upload.html")

    uploaded_file = request.files.get("form_file")
    if not uploaded_file or not uploaded_file.filename:
        flash("Wybierz plik formularza.", "error")
        return render_template("admin/forms/upload.html"), 400
    suffix = Path(uploaded_file.filename).suffix.lower()
    if suffix not in {".json", ".html", ".docx"}:
        flash("Dozwolone formaty to JSON, HTML i DOCX.", "error")
        return render_template("admin/forms/upload.html"), 400
    try:
        form_definition = parse_uploaded_form_definition(uploaded_file.read(), uploaded_file.filename)
        validate_form_definition(form_definition)
        form_definition = normalize_form_definition(form_definition)
    except Exception:
        flash("Niepoprawny plik definicji formularza albo brak wykrytych pol.", "error")
        return render_template("admin/forms/upload.html"), 400

    slug = request.form.get("slug", "").strip() or Path(uploaded_file.filename).stem
    slug = normalize_slug(slug)
    title = request.form.get("name", "").strip() or form_definition.get("title") or slug
    is_active = request.form.get("is_active", "on") == "on"
    is_public = request.form.get("is_public", "on") == "on"
    with db_session_factory()() as db:
        if db.execute(select(Form).where(Form.slug == slug)).scalar_one_or_none():
            flash("Formularz o takim slugu juz istnieje.", "error")
            return render_template("admin/forms/upload.html"), 400
        form = Form(
            slug=slug,
            name=title,
            title=form_definition.get("title", title),
            description=form_definition.get("description", ""),
            definition_json=form_definition,
            created_by_id=g.admin_user.id,
            is_active=is_active,
            is_public=is_public,
            label_text=request.form.get("label_text", "").strip(),
            label_color=request.form.get("label_color", "").strip() or "#b38d45",
            label_background=request.form.get("label_background", "").strip() or "#f7f3ec",
            sort_order=parse_int(request.form.get("sort_order"), 0),
        )
        db.add(form)
        db.flush()
        sync_form_fields(db, form, form_definition)
        db.add(FormPermission(user_id=g.admin_user.id, form_id=form.id, can_manage=True))
        db.commit()
        form_id = form.id
    flash("Formularz zostal wgrany, a pola zostaly wykryte.", "success")
    return redirect(url_for("admin.form_fields", form_id=form_id))


@bp.route("/forms/<int:form_id>/edit", methods=["GET", "POST"])
@login_required
def form_edit(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        users = db.execute(select(User).order_by(User.email)).scalars().all()
        logos = list_selectable_logos(db, g.admin_user, form.logo_id)
        if request.method == "POST":
            form.name = request.form.get("name", "").strip() or form.name
            form.title = request.form.get("title", "").strip() or form.title
            new_slug = normalize_slug(request.form.get("slug", form.slug))
            if new_slug != form.slug and db.execute(select(Form).where(Form.slug == new_slug)).scalar_one_or_none():
                flash("Formularz o takim slugu juz istnieje.", "error")
                assigned_user_ids = {permission.user_id for permission in form.permissions}
                fields = active_fields_for_form(db, form.id)
                return render_template(
                    "admin/forms/edit.html",
                    form=form,
                    fields=fields,
                    users=users,
                    assigned_user_ids=assigned_user_ids,
                    logos=logos,
                ), 400
            form.slug = new_slug
            form.description = request.form.get("description", "").strip()
            form.is_active = request.form.get("is_active") == "on"
            form.is_public = request.form.get("is_public") == "on"
            form.label_text = request.form.get("label_text", "").strip()
            form.label_variant = request.form.get("label_variant", "").strip() or "project"
            form.label_color = request.form.get("label_color", "").strip() or "#b38d45"
            form.label_background = request.form.get("label_background", "").strip() or "#f7f3ec"
            form.sort_order = parse_int(request.form.get("sort_order"), 0)
            selected_logo_id = parse_optional_int(request.form.get("logo_id"))
            if selected_logo_id and not can_select_logo(db, g.admin_user, selected_logo_id):
                abort(403)
            form.logo_id = selected_logo_id
            if g.admin_user.role == ROLE_SUPER_ADMIN:
                selected_user_ids = {int(item) for item in request.form.getlist("user_ids") if item.isdigit()}
                existing = {permission.user_id: permission for permission in form.permissions}
                for user in users:
                    if user.id in selected_user_ids and user.id not in existing:
                        db.add(FormPermission(user_id=user.id, form_id=form.id, can_manage=True))
                    if user.id not in selected_user_ids and user.id in existing:
                        db.delete(existing[user.id])
            db.commit()
            flash("Formularz zostal zapisany.", "success")
            return redirect(url_for("admin.forms_list"))
        assigned_user_ids = {permission.user_id for permission in form.permissions}
        fields = active_fields_for_form(db, form.id)
        return render_template(
            "admin/forms/edit.html",
            form=form,
            fields=fields,
            users=users,
            assigned_user_ids=assigned_user_ids,
            logos=logos,
        )


@bp.post("/forms/<int:form_id>/toggle")
@login_required
def form_toggle(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        form.is_active = not form.is_active
        db.commit()
        flash("Formularz zostal aktywowany." if form.is_active else "Formularz zostal dezaktywowany.", "success")
    return redirect(url_for("admin.forms_list"))


@bp.route("/forms/<int:form_id>/fields", methods=["GET", "POST"])
@login_required
def form_fields(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        fields = active_fields_for_form(db, form.id)
        if request.method == "POST":
            action = request.form.get("action", "save")
            if action == "add":
                field_name = normalize_slug(request.form.get("new_name", "")).replace("-", "_")
                if not field_name:
                    flash("Podaj nazwe pola.", "error")
                    return redirect(url_for("admin.form_fields", form_id=form.id))
                existing = db.execute(
                    select(FormField).where(FormField.form_id == form.id, FormField.name == field_name)
                ).scalar_one_or_none()
                if existing:
                    existing.active = True
                    existing.label = request.form.get("new_label", "").strip() or existing.label or field_name
                    existing.type = request.form.get("new_type", "text") if request.form.get("new_type") in FIELD_TYPES else "text"
                    existing.required = request.form.get("new_required") == "on"
                    existing.section = request.form.get("new_section", "").strip()
                    existing.sort_order = parse_int(request.form.get("new_sort_order"), len(fields) + 1)
                    existing.options = parse_field_options(existing.type, request.form.get("new_options", ""))
                else:
                    db.add(
                        FormField(
                            form_id=form.id,
                            name=field_name,
                            label=request.form.get("new_label", "").strip() or field_name,
                            type=request.form.get("new_type", "text") if request.form.get("new_type") in FIELD_TYPES else "text",
                            required=request.form.get("new_required") == "on",
                            section=request.form.get("new_section", "").strip(),
                            sort_order=parse_int(request.form.get("new_sort_order"), len(fields) + 1),
                            options=parse_field_options(request.form.get("new_type", "text"), request.form.get("new_options", "")),
                            active=True,
                        )
                    )
                db.commit()
                flash("Pole formularza zostalo dodane.", "success")
                return redirect(url_for("admin.form_fields", form_id=form.id))
            if action.startswith("delete:"):
                field_id = parse_optional_int(action.split(":", 1)[1])
                field = db.get(FormField, field_id) if field_id else None
                if not field or field.form_id != form.id:
                    abort(404)
                field.active = False
                db.commit()
                flash("Pole zostalo ukryte. Dane historyczne pozostaja w zgloszeniach.", "success")
                return redirect(url_for("admin.form_fields", form_id=form.id))

            for field in fields:
                prefix = f"field_{field.id}_"
                field.label = request.form.get(prefix + "label", "").strip() or field.name
                field.type = request.form.get(prefix + "type", "").strip() if request.form.get(prefix + "type") in FIELD_TYPES else field.type
                field.required = request.form.get(prefix + "required") == "on"
                field.section = request.form.get(prefix + "section", "").strip()
                field.sort_order = parse_int(request.form.get(prefix + "sort_order"), field.sort_order)
                field.options = parse_field_options(field.type, request.form.get(prefix + "options", ""))
            db.commit()
            flash("Pola formularza zostaly zapisane.", "success")
            return redirect(url_for("admin.form_fields", form_id=form.id))
        return render_template(
            "admin/forms/fields.html",
            form=form,
            fields=fields,
            field_types=FIELD_TYPES,
            field_options_text=field_options_text,
        )


@bp.get("/forms/<int:form_id>/submissions")
@login_required
def submissions_list(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id)
        fields = active_fields_for_form(db, form.id)
        submissions = db.execute(
            select(FormSubmission).where(FormSubmission.form_slug == form.slug)
        ).scalars().all()
        submissions = filter_submissions(submissions, request.args)
        submissions = sort_submissions(submissions, request.args.get("sort") or "created_at", request.args.get("direction") or "desc")
        return render_template(
            "admin/submissions/list.html",
            form=form,
            fields=fields,
            submissions=submissions,
            filters=request.args,
            submission_value=submission_value,
            filter_fields=build_filter_fields(fields, submissions),
            officer_decisions=OFFICER_DECISIONS,
        )


@bp.route("/forms/<int:form_id>/submissions/<int:submission_pk>", methods=["GET", "POST"])
@login_required
def submission_detail(form_id: int, submission_pk: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id)
        submission = db.get(FormSubmission, submission_pk) or abort(404)
        if submission.form_slug != form.slug:
            abort(404)
        if request.method == "POST":
            submission.process_status = request.form.get("process_status", "").strip() or submission.process_status
            submission.officer_decision = request.form.get("officer_decision", "").strip()
            submission.updated_at = datetime.now(timezone.utc)
            db.commit()
            flash("Status zgloszenia zostal zmieniony.", "success")
            return redirect(url_for("admin.submission_detail", form_id=form.id, submission_pk=submission.id))
        files = db.execute(
            select(SubmissionFile).where(SubmissionFile.public_submission_id == submission.submission_id)
        ).scalars().all()
        return render_template("admin/submissions/detail.html", form=form, submission=submission, files=files)


@bp.post("/forms/<int:form_id>/submissions/<int:submission_pk>/decision")
@login_required
def submission_decision_update(form_id: int, submission_pk: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id)
        submission = db.get(FormSubmission, submission_pk) or abort(404)
        if submission.form_slug != form.slug:
            abort(404)
        decision = request.form.get("officer_decision", "").strip()
        allowed_decisions = {value for value, _ in OFFICER_DECISIONS}
        if decision not in allowed_decisions:
            abort(400)
        submission.officer_decision = decision
        submission.officer_decision_reason = request.form.get("officer_decision_reason", "").strip()
        submission.updated_at = datetime.now(timezone.utc)
        db.commit()
        flash("Decyzja urzednika zostala zapisana.", "success")
    return redirect(request.form.get("next") or url_for("admin.submissions_list", form_id=form_id))


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
            log = send_admin_mail(db, form, submission, templates, footers)
            db.add(log)
            db.commit()
            flash("Mail wyslany." if log.status == "sent" else "Nie udalo sie wyslac maila.", "success" if log.status == "sent" else "error")
            return redirect(url_for("admin.submission_detail", form_id=form.id, submission_pk=submission.id))
        return render_template("admin/submissions/mail.html", form=form, submission=submission, templates=templates, footers=footers)


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
        submissions = db.execute(
            select(FormSubmission).where(FormSubmission.form_slug == form.slug)
        ).scalars().all()
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
            log = send_selected_submission_mail(
                db,
                form,
                submission,
                templates,
                footers,
                trigger_event=request.form.get("trigger_event", "manual_bulk").strip() or "manual_bulk",
            )
            summary[log.status] = summary.get(log.status, 0) + 1
            db.add(log)
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


@bp.route("/forms/<int:form_id>/mail-templates/import-html", methods=["GET", "POST"])
@login_required
def mail_template_import_html(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        if request.method == "GET":
            return render_template(
                "admin/mail_templates/import_html.html",
                form=form,
                officer_decisions=OFFICER_DECISIONS,
                template_types=MAIL_TEMPLATE_TYPES,
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
                officer_decisions=OFFICER_DECISIONS,
                template_types=MAIL_TEMPLATE_TYPES,
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


@bp.route("/logos", methods=["GET", "POST"])
@login_required
def logos_list():
    with db_session_factory()() as db:
        if request.method == "POST":
            if g.admin_user.role != ROLE_SUPER_ADMIN:
                abort(403)
            uploaded_file = request.files.get("logo_file")
            if not uploaded_file or not uploaded_file.filename:
                flash("Wybierz plik logo.", "error")
                return redirect(url_for("admin.logos_list"))
            mime_type = uploaded_file.mimetype or mimetypes.guess_type(uploaded_file.filename)[0] or ""
            if mime_type not in LOGO_MIME_TYPES:
                flash("Dozwolone sa tylko pliki graficzne logo.", "error")
                return redirect(url_for("admin.logos_list"))
            logo_bytes = uploaded_file.read()
            if not logo_bytes:
                flash("Plik logo jest pusty.", "error")
                return redirect(url_for("admin.logos_list"))
            logo_dir = Path(current_app.config["TEMP_DIR"]) / "logos"
            logo_dir.mkdir(parents=True, exist_ok=True)
            safe_filename = f"{secrets.token_hex(8)}_{safe_asset_filename(uploaded_file.filename)}"
            storage_path = logo_dir / safe_filename
            storage_path.write_bytes(logo_bytes)
            db.add(
                Logo(
                    name=request.form.get("name", "").strip() or Path(uploaded_file.filename).stem,
                    filename=Path(uploaded_file.filename).name,
                    storage_path=str(storage_path),
                    mime_type=mime_type,
                    size_bytes=len(logo_bytes),
                    checksum_sha256=hashlib.sha256(logo_bytes).hexdigest(),
                    uploaded_by_user_id=g.admin_user.id,
                    active=True,
                )
            )
            db.commit()
            flash("Logo zostalo dodane.", "success")
            return redirect(url_for("admin.logos_list"))

        query = select(Logo).order_by(Logo.created_at.desc())
        if g.admin_user.role != ROLE_SUPER_ADMIN:
            query = query.where(Logo.active.is_(True))
        logos = db.execute(query).scalars().all()
        return render_template("admin/logos/list.html", logos=logos)


@bp.post("/logos/<int:logo_id>/toggle")
@login_required
@role_required(ROLE_SUPER_ADMIN)
def logo_toggle(logo_id: int):
    with db_session_factory()() as db:
        logo = db.get(Logo, logo_id) or abort(404)
        logo.active = not logo.active
        db.commit()
    flash("Logo zostalo aktywowane." if logo.active else "Logo zostalo dezaktywowane.", "success")
    return redirect(url_for("admin.logos_list"))


@bp.route("/logos/<int:logo_id>/edit", methods=["GET", "POST"])
@login_required
@role_required(ROLE_SUPER_ADMIN)
def logo_edit(logo_id: int):
    with db_session_factory()() as db:
        logo = db.get(Logo, logo_id)
        if not logo:
            abort(404)
        if request.method == "POST":
            logo.name = request.form.get("name", "").strip() or logo.name
            logo.active = request.form.get("active") == "on"
            db.commit()
            flash("Logo zostalo zapisane.", "success")
            return redirect(url_for("admin.logos_list"))
        return render_template("admin/logos/edit.html", logo=logo)


@bp.get("/logos/<int:logo_id>/asset")
@login_required
def logo_asset(logo_id: int):
    with db_session_factory()() as db:
        logo = db.get(Logo, logo_id)
        if not logo or (g.admin_user.role != ROLE_SUPER_ADMIN and not logo.active):
            abort(404)
        logo_path = Path(logo.storage_path)
        if not logo_path.exists():
            abort(404)
        return send_file(logo_path, mimetype=logo.mime_type or None)


@bp.route("/users")
@login_required
@role_required(ROLE_SUPER_ADMIN)
def users_list():
    with db_session_factory()() as db:
        users = db.execute(select(User).order_by(User.email)).scalars().all()
        forms = db.execute(select(Form).order_by(Form.name)).scalars().all()
        return render_template("admin/users/list.html", users=users, forms=forms)


@bp.post("/users/<int:user_id>/toggle-block")
@login_required
@role_required(ROLE_SUPER_ADMIN)
def user_toggle_block(user_id: int):
    with db_session_factory()() as db:
        user = db.get(User, user_id) or abort(404)
        if user.id == g.admin_user.id:
            flash("Nie mozna zablokowac aktualnie zalogowanego uzytkownika.", "error")
            return redirect(url_for("admin.users_list"))
        user.is_blocked = not user.is_blocked
        db.commit()
        flash("Uzytkownik zostal zablokowany." if user.is_blocked else "Uzytkownik zostal odblokowany.", "success")
    return redirect(url_for("admin.users_list"))


@bp.route("/users/new", methods=["GET", "POST"])
@bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@role_required(ROLE_SUPER_ADMIN)
def user_edit(user_id: int | None = None):
    with db_session_factory()() as db:
        user = db.get(User, user_id) if user_id else User(email="", password_hash="", role=ROLE_FORM_MANAGER)
        if not user:
            abort(404)
        forms = db.execute(select(Form).order_by(Form.name)).scalars().all()
        if request.method == "POST":
            user.email = request.form.get("email", "").strip().lower()
            user.role = request.form.get("role", ROLE_FORM_MANAGER)
            if user.role not in ROLES:
                abort(400)
            password = request.form.get("password", "")
            if password:
                user.password_hash = generate_password_hash(password)
            if not user.password_hash:
                flash("Haslo jest wymagane dla nowego uzytkownika.", "error")
                return render_template("admin/users/edit.html", user=user, roles=sorted(ROLES), forms=forms, assigned_form_ids=set()), 400
            user.is_active = request.form.get("is_active") == "on"
            user.is_blocked = request.form.get("is_blocked") == "on"
            db.add(user)
            db.flush()
            selected_form_ids = {int(item) for item in request.form.getlist("form_ids") if item.isdigit()}
            existing = {permission.form_id: permission for permission in user.permissions}
            for form in forms:
                if form.id in selected_form_ids and form.id not in existing:
                    db.add(FormPermission(user_id=user.id, form_id=form.id, can_manage=True))
                if form.id not in selected_form_ids and form.id in existing:
                    db.delete(existing[form.id])
            db.commit()
            flash("Uzytkownik zostal zapisany.", "success")
            return redirect(url_for("admin.users_list"))
        assigned_form_ids = {permission.form_id for permission in user.permissions}
        return render_template("admin/users/edit.html", user=user, roles=sorted(ROLES), forms=forms, assigned_form_ids=assigned_form_ids)


def normalize_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip().lower()).strip("_")
    return slug or f"form_{secrets.token_hex(4)}"


def safe_asset_filename(value: str) -> str:
    filename = Path(value or "asset").name
    filename = re.sub(r"[^a-zA-Z0-9_.-]+", "_", filename).strip("._")
    return filename or f"asset_{secrets.token_hex(4)}"


def read_uploaded_template_file(field_name: str, allowed_suffixes: set[str]) -> str | None:
    uploaded_file = request.files.get(field_name)
    if not uploaded_file or not uploaded_file.filename:
        return None
    suffix = Path(uploaded_file.filename).suffix.lower()
    if suffix not in allowed_suffixes:
        abort(400)
    return uploaded_file.read().decode("utf-8-sig", errors="ignore")


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return default


def parse_optional_int(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def active_fields_for_form(db, form_id: int) -> list[FormField]:
    return db.execute(
        select(FormField)
        .where(FormField.form_id == form_id, FormField.active.is_(True))
        .order_by(FormField.sort_order, FormField.id)
    ).scalars().all()


def parse_field_options(field_type: str, raw_text: str) -> list:
    if field_type not in {"select", "radio", "checkbox"}:
        return []
    options = []
    for line in str(raw_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if field_type == "checkbox":
            value, _, label = line.partition("|")
            value = value.strip()
            label = label.strip() or value
            if value:
                options.append({"value": value, "label": label})
        else:
            options.append(line)
    return options


def field_options_text(field: FormField) -> str:
    options = field.options if isinstance(field.options, list) else []
    lines = []
    for option in options:
        if isinstance(option, dict):
            value = str(option.get("value") or option.get("label") or "").strip()
            label = str(option.get("label") or value).strip()
            lines.append(value if value == label else f"{value}|{label}")
        else:
            lines.append(str(option))
    return "\n".join(line for line in lines if line)


def list_selectable_logos(db, user: User, current_logo_id: int | None = None) -> list[Logo]:
    query = select(Logo).order_by(Logo.name)
    if user.role != ROLE_SUPER_ADMIN:
        query = query.where(Logo.active.is_(True))
    logos = db.execute(query).scalars().all()
    if current_logo_id and current_logo_id not in {logo.id for logo in logos}:
        current = db.get(Logo, current_logo_id)
        if current and user.role == ROLE_SUPER_ADMIN:
            logos.append(current)
    return logos


def list_active_logos(db) -> list[Logo]:
    return db.execute(select(Logo).where(Logo.active.is_(True)).order_by(Logo.name)).scalars().all()


def can_select_logo(db, user: User, logo_id: int) -> bool:
    logo = db.get(Logo, logo_id)
    if not logo:
        return False
    return user.role == ROLE_SUPER_ADMIN or logo.active


def can_select_active_logo(db, logo_id: int) -> bool:
    logo = db.get(Logo, logo_id)
    return bool(logo and logo.active)


def build_footer_html(footer: MailFooter | None) -> str:
    if not footer:
        return ""
    parts = []
    if footer.logo and footer.logo.active:
        logo_url = url_for(
            "public_forms.logo_asset",
            logo_id=footer.logo.id,
            filename=footer.logo.filename,
            _external=True,
        )
        parts.append(
            '<div style="margin-bottom:16px;">'
            f'<img src="{escape(logo_url)}" alt="{escape(footer.logo.name)}" '
            'style="display:block;max-width:180px;max-height:80px;width:auto;height:auto;">'
            "</div>"
        )
    if footer.html_body:
        parts.append(footer.html_body)
    return "\n".join(parts)


def parse_uploaded_form_definition(content: bytes, filename: str) -> dict:
    suffix = Path(filename).suffix.lower()
    if suffix == ".json":
        return json.loads(content.decode("utf-8-sig"))
    if suffix == ".html":
        return build_definition_from_html(content.decode("utf-8-sig", errors="ignore"), filename)
    if suffix == ".docx":
        return build_definition_from_docx(content, filename)
    raise ValueError("unsupported format")


def build_definition_from_html(html: str, filename: str) -> dict:
    fields: list[dict] = []
    input_pattern = re.compile(r"<(input|select|textarea)\b([^>]*)>", re.IGNORECASE | re.DOTALL)
    for tag, attrs in input_pattern.findall(html):
        name = html_attr(attrs, "name")
        if not name or name.startswith("_") or name == "csrf_token":
            continue
        field_type = tag.lower()
        if tag.lower() == "input":
            field_type = html_attr(attrs, "type") or "text"
        fields.append(
            {
                "type": field_type,
                "name": name,
                "label": humanize_field_name(name),
                "required": "required" in attrs.lower(),
            }
        )
    if not fields:
        raise ValueError("no fields")
    return {"title": Path(filename).stem, "fields": fields}


def build_definition_from_docx(content: bytes, filename: str) -> dict:
    with zipfile.ZipFile(BytesIO(content)) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    texts = [item.text or "" for item in root.iter() if item.tag.endswith("}t") and item.text]
    raw = "\n".join(texts)
    candidates = re.findall(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", raw)
    fields = [
        {"type": "text", "name": name, "label": humanize_field_name(name), "required": False}
        for name in dict.fromkeys(candidates)
    ]
    if not fields:
        raise ValueError("no fields")
    return {"title": Path(filename).stem, "fields": fields}


def html_attr(attrs: str, name: str) -> str:
    match = re.search(rf'\b{name}\s*=\s*["\']([^"\']+)["\']', attrs, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def humanize_field_name(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()


def sync_form_fields(db, form: Form, form_definition: dict) -> None:
    existing_fields = {field.name: field for field in form.fields}
    for field in existing_fields.values():
        field.active = False
    current_section = ""
    order = 0
    for field in detect_form_fields(form_definition):
        if field.get("type") == "section":
            current_section = field.get("label", "")
            continue
        name = field.get("name")
        if not name:
            continue
        form_field = existing_fields.get(name) or FormField(form_id=form.id, name=name)
        form_field.label = field.get("label", name)
        form_field.type = field.get("type", "text")
        form_field.required = bool(field.get("required"))
        form_field.options = field.get("options") or []
        form_field.default_value = str(field.get("default", ""))
        form_field.section = current_section
        form_field.sort_order = order
        form_field.active = True
        db.add(form_field)
        order += 1


def detect_form_fields(form_definition: dict) -> list[dict]:
    fields = list(form_definition.get("fields") or [])
    documents = ((form_definition.get("process") or {}).get("documents") or form_definition.get("documents") or {})
    if isinstance(documents, dict):
        for document in documents.values():
            if isinstance(document, dict):
                fields.extend(document.get("fields") or [])
    return fields


def accessible_form_ids(db, user: User) -> list[int]:
    if user.role == ROLE_SUPER_ADMIN:
        return [item for item in db.execute(select(Form.id)).scalars().all()]
    return [item for item in db.execute(select(FormPermission.form_id).where(FormPermission.user_id == user.id)).scalars().all()]


def accessible_form_slugs(db, form_ids: list[int]) -> list[str]:
    if not form_ids:
        return []
    return [item for item in db.execute(select(Form.slug).where(Form.id.in_(form_ids))).scalars().all()]


def count_accessible_forms(db, user: User, form_ids: list[int]) -> int:
    if user.role == ROLE_SUPER_ADMIN:
        return db.execute(select(func.count(Form.id))).scalar() or 0
    return len(form_ids)


def list_accessible_forms(db, user: User) -> list[Form]:
    if user.role == ROLE_SUPER_ADMIN:
        return db.execute(select(Form).order_by(Form.sort_order, Form.name)).scalars().all()
    form_ids = accessible_form_ids(db, user)
    if not form_ids:
        return []
    return db.execute(select(Form).where(Form.id.in_(form_ids)).order_by(Form.sort_order, Form.name)).scalars().all()


def ensure_form_access(db, form_id: int, manage: bool = False) -> Form:
    form = db.get(Form, form_id)
    if not form:
        abort(404)
    user = g.admin_user
    if user.role == ROLE_SUPER_ADMIN:
        return form
    permission = db.execute(
        select(FormPermission).where(FormPermission.form_id == form_id, FormPermission.user_id == user.id)
    ).scalar_one_or_none()
    if not permission or (manage and not permission.can_manage):
        abort(403)
    return form


def submission_value(submission: FormSubmission, field_name: str) -> Any:
    if hasattr(submission, field_name):
        return getattr(submission, field_name)
    return (submission.data_json or {}).get(field_name, "")


def build_filter_fields(fields: list[FormField], submissions: list[FormSubmission]) -> list[tuple[str, str]]:
    technical_fields = [
        ("created_at", "Data utworzenia"),
        ("process_status", "Status procesu"),
        ("officer_decision", "Decyzja urzednika"),
        ("email", "E-mail"),
        ("nazwisko", "Nazwisko"),
        ("submission_id", "ID zgloszenia"),
    ]
    seen = {name for name, _ in technical_fields}
    result = list(technical_fields)
    for field in fields:
        if field.name not in seen:
            result.append((field.name, field.label or field.name))
            seen.add(field.name)
    for submission in submissions:
        for key in (submission.data_json or {}).keys():
            if key not in seen:
                result.append((key, key))
                seen.add(key)
    return result


def filter_submissions(submissions: list[FormSubmission], args) -> list[FormSubmission]:
    q = str(args.get("q") or "").strip().lower()
    status = str(args.get("status") or "").strip()
    field = str(args.get("field") or "").strip()
    operator = str(args.get("operator") or "contains").strip()
    value = str(args.get("value") or "").strip()
    value_to = str(args.get("value_to") or "").strip()

    def matches(submission: FormSubmission) -> bool:
        if status and submission.process_status != status:
            return False
        if field and not matches_field_filter(submission_value(submission, field), operator, value, value_to):
            return False
        if not q:
            return True
        haystack = [
            submission.submission_id,
            submission.email,
            submission.nazwisko,
            submission.process_status,
            submission.officer_decision,
            *(str(item) for item in (submission.data_json or {}).values()),
        ]
        return any(q in str(item).lower() for item in haystack)

    return [submission for submission in submissions if matches(submission)]


def matches_field_filter(raw_value: Any, operator: str, expected: str, expected_to: str = "") -> bool:
    value_text = "" if raw_value is None else str(raw_value)
    value_lower = value_text.lower()
    expected_lower = expected.lower()
    operator = operator or "contains"

    if operator == "empty":
        return value_text.strip() == ""
    if operator == "not_empty":
        return value_text.strip() != ""
    if operator == "equals":
        return value_lower == expected_lower
    if operator == "not_equals":
        return value_lower != expected_lower
    if operator == "not_contains":
        return expected_lower not in value_lower
    if operator == "date_range":
        return matches_date_range(raw_value, expected, expected_to)
    return expected_lower in value_lower


def matches_date_range(raw_value: Any, expected_from: str, expected_to: str) -> bool:
    value_date = parse_date_value(raw_value)
    if not value_date:
        return False
    from_date = parse_date_value(expected_from)
    to_date = parse_date_value(expected_to)
    if from_date and value_date < from_date:
        return False
    if to_date and value_date > to_date:
        return False
    return True


def parse_date_value(value: Any):
    if not value:
        return None
    if hasattr(value, "date"):
        return value.date()
    text = str(value).strip()
    for candidate in [text, text[:10]]:
        try:
            return datetime.fromisoformat(candidate).date()
        except ValueError:
            continue
    return None


def sort_submissions(submissions: list[FormSubmission], sort_field: str, direction: str) -> list[FormSubmission]:
    reverse = direction != "asc"

    def sort_key(submission: FormSubmission):
        value = submission_value(submission, sort_field)
        return "" if value is None else str(value).lower()

    if sort_field == "created_at":
        return sorted(submissions, key=lambda item: item.created_at or datetime.min, reverse=reverse)
    return sorted(submissions, key=sort_key, reverse=reverse)


def build_mail_context(form: Form, submission: FormSubmission | None, files: list[SubmissionFile]) -> dict:
    context = build_platform_mail_context(form, submission, files)
    if submission:
        context["podpisz_url"] = url_for("documents.documents_to_sign", submission_id=submission.submission_id, _external=True)
        if submission.pdf_filename and current_app.extensions.get("services"):
            try:
                document_url = current_app.extensions["services"].document_service.build_download_url(
                    {"form_slug": submission.form_slug, "submission_id": submission.submission_id, "access_token": submission.access_token},
                    submission.pdf_filename,
                )
                context["document_url"] = document_url
                context["pobierz_url"] = document_url
            except Exception:
                context["document_url"] = ""
                context["pobierz_url"] = ""
    return context


def render_mail_text(raw_text: str, context: dict) -> str:
    return render_template_text(raw_text or "", context)


def preview_mail_context(form: Form, submission: FormSubmission | None = None) -> dict:
    context = build_mail_context(form, submission, [])
    context.setdefault("imiona", "Jan")
    context.setdefault("nazwisko", "Kowalski")
    context.setdefault("email", "jan.kowalski@example.com")
    context.setdefault("submission_id", "6ef64b28-530b-4f8e-9325-26ae83e7c11e")
    context.setdefault("form_name", form.name)
    context.setdefault("form_slug", form.slug)
    context.setdefault("process_status", "FORM_SUBMITTED")
    context.setdefault("status_label", "Wniosek zaakceptowany")
    context.setdefault("officer_decision", "accepted")
    context.setdefault("declaration_filename", "deklaracja.pdf")
    context.setdefault("agreement_filename", "umowa.pdf")
    context.setdefault("podpisz_url", url_for("documents.documents_to_sign", submission_id=context["submission_id"], _external=True))
    context.setdefault("pobierz_url", "")
    context.setdefault("document_url", "")
    return context


def send_admin_mail(db, form: Form, submission: FormSubmission, templates: list[MailTemplate], footers: list[MailFooter]) -> EmailLog:
    template_id = int(request.form.get("template_id") or 0)
    footer_id = int(request.form.get("footer_id") or 0)
    template = next((item for item in templates if item.id == template_id), None)
    footer = next((item for item in footers if item.id == footer_id), None)
    subject_template = request.form.get("subject", "").strip() or (template.subject if template else "")
    body_template = request.form.get("body_html", request.form.get("html_body", "")).strip() or (template.html_body if template else "")
    text_template = request.form.get("body_text", request.form.get("text_body", "")).strip() or (template.text_body if template and template.text_body else body_template)
    render_template = template or SimpleNamespace(
        id=None,
        name=subject_template or "Mail",
        content_title=request.form.get("content_title", "").strip() or subject_template,
        html_body=body_template,
        text_body=text_template,
        instruction_html=request.form.get("instruction_html", "").strip(),
        instruction_text=request.form.get("instruction_text", "").strip(),
        footer_note=request.form.get("footer_note", "").strip(),
    )
    to_email = request.form.get("to_email", "").strip() or submission.email
    return send_mail_for_submission(
        db,
        form,
        submission,
        template=render_template,
        log_template=template,
        footer=footer,
        to_email=to_email,
        subject_template=subject_template,
    )


def send_selected_submission_mail(
    db,
    form: Form,
    submission: FormSubmission,
    templates: list[MailTemplate],
    footers: list[MailFooter],
    *,
    trigger_event: str,
) -> EmailLog:
    template = select_mail_template(templates, submission, trigger_event)
    footer = next((item for item in footers if item.is_default), None)
    if not template:
        return EmailLog(
            form_id=form.id,
            submission_id=submission.id,
            public_submission_id=submission.submission_id,
            to_email=submission.email,
            subject="",
            sent_by_id=g.admin_user.id,
            status="skipped",
            error_message="Brak szablonu maila dla statusu/decyzji.",
        )
    return send_mail_for_submission(
        db,
        form,
        submission,
        template=template,
        log_template=template,
        footer=footer,
        to_email=submission.email,
        subject_template=template.subject,
    )


def select_mail_template(templates: list[MailTemplate], submission: FormSubmission, trigger_event: str) -> MailTemplate | None:
    candidates = []
    for template in templates:
        if template.is_active is False:
            continue
        type_match_score = mail_template_type_score(template, submission)
        if template.trigger_event and template.trigger_event != trigger_event:
            continue
        if template.trigger_status and template.trigger_status != submission.process_status:
            continue
        if template.trigger_decision and template.trigger_decision != submission.officer_decision:
            continue
        if not any([template.trigger_event, template.trigger_status, template.trigger_decision, template.is_default_for_status, type_match_score]):
            continue
        score = 0
        score += 8 if template.trigger_event == trigger_event else 0
        score += 4 if template.trigger_decision and template.trigger_decision == submission.officer_decision else 0
        score += 2 if template.trigger_status and template.trigger_status == submission.process_status else 0
        score += type_match_score
        score += 1 if template.is_default_for_status else 0
        candidates.append((score, template.id, template))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (item[0], item[1]), reverse=True)[0][2]


def mail_template_type_score(template: MailTemplate, submission: FormSubmission) -> int:
    template_type = (template.template_type or "").strip()
    if template_type == "accepted" and submission.officer_decision == "accepted":
        return 3
    if template_type == "rejected" and submission.officer_decision == "rejected":
        return 3
    if template_type == "correction_required" and submission.process_status == "CORRECTION_REQUIRED":
        return 3
    if template_type == "confirmation" and submission.process_status == "FORM_SUBMITTED":
        return 3
    if template_type == "declaration_signed" and str(submission.declaration_signed).lower() == "tak":
        return 3
    if template_type == "agreement_ready" and submission.process_status == "AGREEMENT_READY":
        return 3
    if template_type == "agreement_signed_by_user" and str(submission.agreement_signed).lower() == "tak":
        return 3
    office_signed = (
        getattr(submission, "office_agreement_signed", "")
        or getattr(submission, "office_agreement_signed_email_sent", "")
        or getattr(submission, "office_agreement_signed_email_sent_for", "")
    )
    if template_type == "agreement_signed_by_office" and str(office_signed).lower() == "tak":
        return 3
    return 0


def send_mail_for_submission(
    db,
    form: Form,
    submission: FormSubmission,
    *,
    template: MailTemplate | None,
    footer: MailFooter | None,
    to_email: str,
    subject_template: str,
    log_template: MailTemplate | None = None,
) -> EmailLog:
    files = db.execute(select(SubmissionFile).where(SubmissionFile.public_submission_id == submission.submission_id)).scalars().all()
    context = build_mail_context(form, submission, files)
    subject = render_mail_text(subject_template, context)
    footer_html = build_footer_html(footer)
    html_body = render_platform_mail_html(template, context, footer_html=footer_html)
    text_body = render_platform_mail_text(template, context)
    log = EmailLog(
        form_id=form.id,
        submission_id=submission.id,
        public_submission_id=submission.submission_id,
        to_email=to_email,
        subject=subject,
        template_id=log_template.id if log_template else None,
        footer_id=footer.id if footer else None,
        sent_by_id=g.admin_user.id,
        status="sent",
    )
    try:
        current_app.extensions["services"].notification_service.smtp_sender(
            smtp_host=current_app.config["SMTP_HOST"],
            smtp_port=current_app.config["SMTP_PORT"],
            smtp_user=current_app.config["SMTP_USER"],
            smtp_password=current_app.config["SMTP_PASSWORD"],
            mail_from=current_app.config["MAIL_FROM"],
            to_emails=[to_email],
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            use_tls=current_app.config.get("SMTP_USE_TLS", True),
            use_ssl=current_app.config.get("SMTP_USE_SSL", False),
            timeout=current_app.config.get("SMTP_TIMEOUT", 30),
        )
    except Exception as exc:
        log.status = "failed"
        log.error_message = type(exc).__name__
    return log
