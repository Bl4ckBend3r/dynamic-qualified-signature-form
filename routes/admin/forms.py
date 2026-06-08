from __future__ import annotations

from pathlib import Path

from flask import abort, current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, select

from form_loader import FIELD_STAGE_AFTER_ACCEPTANCE, FIELD_STAGE_INITIAL
from models import Form, FormField, FormPermission, FormSubmission, User
from services.admin_form_service import (
    build_form_definition_from_admin_form,
    normalize_admin_form_definition,
    normalize_field_stage,
    parse_uploaded_form_definition,
    sync_form_fields,
    validate_admin_form_config,
)
from services.form_config_service import TRIGGER_DESCRIPTIONS

from . import (
    ROLE_SUPER_ADMIN,
    active_fields_for_form,
    bp,
    can_select_logo,
    db_session_factory,
    ensure_form_access,
    field_options_text,
    list_accessible_forms,
    list_selectable_logos,
    login_required,
    normalize_slug,
    parse_field_options,
    parse_int,
    parse_optional_int,
    role_required,
)


FIELD_TYPES = ["text", "textarea", "email", "tel", "number", "date", "select", "radio", "checkbox", "pesel"]
FIELD_STAGES = [
    (FIELD_STAGE_INITIAL, "Podstawowe"),
    (FIELD_STAGE_AFTER_ACCEPTANCE, "Dodatkowe pole po akceptacji"),
]


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


@bp.post("/forms/<int:form_id>/delete")
@login_required
@role_required(ROLE_SUPER_ADMIN)
def form_delete(form_id: int):
    with db_session_factory()() as db:
        form = db.get(Form, form_id) or abort(404)
        submissions_count = db.execute(
            select(func.count(FormSubmission.id)).where(FormSubmission.form_slug == form.slug)
        ).scalar() or 0
        if submissions_count:
            flash("Nie można usunąć formularza, ponieważ istnieją powiązane zgłoszenia.", "error")
            return redirect(url_for("admin.forms_list"))
        try:
            db.delete(form)
            db.commit()
            flash("Formularz został usunięty z bazy danych.", "success")
        except Exception:
            db.rollback()
            current_app.logger.exception("Nie udało się usunąć formularza %s", form_id)
            flash(
                "Nie udało się usunąć formularza. Spróbuj ponownie albo skontaktuj się z administratorem technicznym.",
                "error",
            )
    return redirect(url_for("admin.forms_list"))


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
        form_definition = normalize_admin_form_definition(form_definition)
        validation_errors = validate_admin_form_config(form_definition)
        if validation_errors:
            raise ValueError("; ".join(validation_errors))
    except Exception as exc:
        current_app.logger.warning("Niepoprawna definicja formularza: %s", exc)
        flash("Niepoprawny plik definicji formularza albo brak wykrytych pól.", "error")
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
            try:
                updated_definition = build_form_definition_from_admin_form(form.definition_json or {}, request.form)
            except Exception:
                updated_definition = normalize_admin_form_definition(form.definition_json or {})
                validation_errors = ["Workflow JSON ma niepoprawny format. Sprawdź nawiasy, cudzysłowy i przecinki."]
                assigned_user_ids = {permission.user_id for permission in form.permissions}
                fields = active_fields_for_form(db, form.id)
                return render_template(
                    "admin/forms/edit.html",
                    form=form,
                    fields=fields,
                    users=users,
                    assigned_user_ids=assigned_user_ids,
                    logos=logos,
                    workflow_json=request.form.get("workflow_json", ""),
                    trigger_descriptions=TRIGGER_DESCRIPTIONS,
                    validation_errors=validation_errors,
                ), 400
            validation_errors = validate_admin_form_config(updated_definition)
            if validation_errors:
                flash("Nie można zapisać workflow: " + " ".join(validation_errors), "error")
                assigned_user_ids = {permission.user_id for permission in form.permissions}
                fields = active_fields_for_form(db, form.id)
                return render_template(
                    "admin/forms/edit.html",
                    form=form,
                    fields=fields,
                    users=users,
                    assigned_user_ids=assigned_user_ids,
                    logos=logos,
                    workflow_json=format_json(updated_definition.get("workflow") or {}),
                    trigger_descriptions=TRIGGER_DESCRIPTIONS,
                    validation_errors=validation_errors,
                ), 400
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
            form.definition_json = updated_definition
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
        form.definition_json = normalize_admin_form_definition(form.definition_json or {})
        return render_template(
            "admin/forms/edit.html",
            form=form,
            fields=fields,
            users=users,
            assigned_user_ids=assigned_user_ids,
            logos=logos,
            workflow_json=format_json((form.definition_json or {}).get("workflow") or {}),
            trigger_descriptions=TRIGGER_DESCRIPTIONS,
            validation_errors=[],
        )


@bp.post("/forms/<int:form_id>/toggle")
@login_required
def form_toggle(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        form.is_active = not form.is_active
        db.commit()
        is_active = form.is_active
    flash("Formularz zostal aktywowany." if is_active else "Formularz zostal dezaktywowany.", "success")
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
                    existing.stage = normalize_field_stage(request.form.get("new_stage"))
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
                            stage=normalize_field_stage(request.form.get("new_stage")),
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
                field.stage = normalize_field_stage(request.form.get(prefix + "stage"))
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
            field_stages=FIELD_STAGES,
            field_options_text=field_options_text,
        )


def format_json(value) -> str:
    import json

    return json.dumps(value or {}, ensure_ascii=False, indent=2)
