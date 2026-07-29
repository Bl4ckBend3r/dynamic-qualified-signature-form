from __future__ import annotations

import json
from pathlib import Path

from flask import abort, current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, select

from form_loader import FIELD_STAGE_AFTER_ACCEPTANCE, FIELD_STAGE_INITIAL
from models import Form, FormField, FormPermission, FormRegulation, FormSubmission, User
from services.admin_form_service import (
    build_form_definition_from_admin_form,
    get_declaration_training_field,
    normalize_admin_form_definition,
    normalize_field_stage,
    parse_uploaded_form_definition,
    sync_form_fields,
    validate_admin_form_config,
)
from services.form_config_service import TRIGGER_DESCRIPTIONS
from services.process_instruction_service import (
    instruction_status_options,
    normalize_instruction_config,
    reconcile_instruction_config,
)
from services.site_document_service import save_document_upload, update_form_regulation_from_upload
from services.upload_validation import UploadValidationError
from services.workflow_config_service import (
    WorkflowConfigNormalizer,
    repair_agreement_confirmation_path,
    workflow_status_options,
)

from . import (
    ROLE_ADMIN,
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
FORM_EDITOR_TABS = {
    "basic",
    "fields",
    "trainings",
    "declaration",
    "agreement",
    "workflow",
    "instructions",
    "emails",
    "documents",
    "appearance",
    "permissions",
    "advanced",
}
FIELD_STAGES = [
    (FIELD_STAGE_INITIAL, "Podstawowe"),
    (FIELD_STAGE_AFTER_ACCEPTANCE, "Dodatkowe pole po akceptacji"),
]
LOGO_ALIGNMENTS = {"left", "center", "right"}


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
@role_required(ROLE_SUPER_ADMIN)
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
        message = str(exc).strip()
        if suffix == ".docx" and "Nie wykryto" not in message:
            message = (
                "Nie udało się odczytać pól z DOCX. Użyj etykiet zakończonych dwukropkiem, "
                "pustych linii do wypełnienia albo znaczników {{ nazwa_pola }}."
            )
        flash(message or "Plik nie zawiera poprawnej definicji formularza.", "error")
        return render_template("admin/forms/upload.html"), 400

    slug = request.form.get("slug", "").strip() or Path(uploaded_file.filename).stem
    slug = normalize_slug(slug)
    title = request.form.get("name", "").strip() or form_definition.get("title") or slug
    is_active = request.form.get("is_active", "on") == "on"
    is_public = request.form.get("is_public", "on") == "on"
    user_instruction = str(form_definition.pop("user_instruction", "") or "").strip() or None
    instruction_config = normalize_instruction_config(
        form_definition.pop("user_instruction_config", None),
        legacy_description=user_instruction,
    )
    user_instruction = instruction_config["description"] or None
    with db_session_factory()() as db:
        if db.execute(select(Form).where(Form.slug == slug)).scalar_one_or_none():
            flash("Formularz o takim slugu już istnieje.", "error")
            return render_template("admin/forms/upload.html"), 400
        form = Form(
            slug=slug,
            name=title,
            title=form_definition.get("title", title),
            description=form_definition.get("description", ""),
            user_instruction=user_instruction,
            user_instruction_config=instruction_config,
            definition_json=form_definition,
            created_by_id=g.admin_user.id,
            is_active=is_active,
            is_public=is_public,
            label_text=request.form.get("label_text", "").strip(),
            label_color=request.form.get("label_color", "").strip() or "#b38d45",
            label_background=request.form.get("label_background", "").strip() or "#f7f3ec",
            sort_order=parse_int(request.form.get("sort_order"), 0),
        )
        current_app.extensions["services"].mail_settings_service.update_form(form, request.form)
        db.add(form)
        db.flush()
        sync_form_fields(db, form, form_definition)
        db.add(FormPermission(user_id=g.admin_user.id, form_id=form.id, can_manage=True))
        db.commit()
        form_id = form.id
    flash("Formularz został wgrany, a pola zostały wykryte.", "success")
    return redirect(url_for("admin.form_fields", form_id=form_id))


@bp.route("/forms/<int:form_id>/edit", methods=["GET", "POST"])
@login_required
def form_edit(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        form.workflow_ids_locked = bool(
            db.execute(select(func.count(FormSubmission.id)).where(FormSubmission.form_slug == form.slug)).scalar()
        )
        users = db.execute(select(User).order_by(User.email)).scalars().all()
        logos = list_selectable_logos(db, g.admin_user, form.logo_id)
        instruction_statuses = instruction_status_options()
        requested_tab = request.form.get("active_tab") if request.method == "POST" else request.args.get("tab")
        active_tab = _normalize_form_editor_tab(requested_tab, g.admin_user.role)
        if request.method == "POST":
            try:
                instruction_config = _instruction_config_from_admin_form(
                    request.form,
                    existing=form.user_instruction_config,
                    workflow=(form.definition_json or {}).get("workflow") or {},
                    allow_advanced_json=g.admin_user.role == ROLE_SUPER_ADMIN,
                )
                updated_definition = build_form_definition_from_admin_form(
                    form.definition_json or {},
                    request.form,
                    allow_advanced_json=g.admin_user.role == ROLE_SUPER_ADMIN,
                )
                updated_definition.pop("user_instruction", None)
                updated_definition.pop("user_instruction_config", None)
            except Exception as exc:
                updated_definition = normalize_admin_form_definition(form.definition_json or {})
                validation_errors = [str(exc) or "Niepoprawne dane formularza."]
                active_tab = _tab_for_form_error(validation_errors, active_tab, request.form)
                assigned_user_ids = {permission.user_id for permission in form.permissions}
                fields = active_fields_for_form(db, form.id)
                workflow_context = _workflow_editor_context(
                    updated_definition.get("workflow") or {},
                    workflow_json=request.form.get("workflow_json"),
                    instruction_config=form.user_instruction_config,
                )
                return render_template(
                    "admin/forms/edit.html",
                    form=form,
                    fields=fields,
                    users=users,
                    assigned_user_ids=assigned_user_ids,
                    logos=logos,
                    training_field=get_declaration_training_field(updated_definition),
                    trigger_descriptions=TRIGGER_DESCRIPTIONS,
                    instruction_statuses=instruction_statuses,
                    validation_errors=validation_errors,
                    active_tab=active_tab,
                    **workflow_context,
                ), 400
            validation_errors = validate_admin_form_config(
                updated_definition,
                validate_visual_workflow=bool(
                    request.form.get("workflow_builder_json")
                    or request.form.get("workflow_use_advanced_json") == "on"
                ),
            )
            if validation_errors:
                active_tab = _tab_for_form_error(validation_errors, active_tab, request.form)
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
                    training_field=get_declaration_training_field(updated_definition),
                    trigger_descriptions=TRIGGER_DESCRIPTIONS,
                    instruction_statuses=instruction_statuses,
                    validation_errors=validation_errors,
                    active_tab=active_tab,
                    **_workflow_editor_context(
                        updated_definition.get("workflow") or {}, instruction_config=instruction_config
                    ),
                ), 400
            form.name = request.form.get("name", "").strip() or form.name
            form.title = request.form.get("title", "").strip() or form.title
            new_slug = normalize_slug(request.form.get("slug", form.slug))
            if new_slug != form.slug and db.execute(select(Form).where(Form.slug == new_slug)).scalar_one_or_none():
                flash("Formularz o takim slugu już istnieje.", "error")
                assigned_user_ids = {permission.user_id for permission in form.permissions}
                fields = active_fields_for_form(db, form.id)
                return render_template(
                    "admin/forms/edit.html",
                    form=form,
                    fields=fields,
                    users=users,
                    assigned_user_ids=assigned_user_ids,
                    logos=logos,
                    training_field=get_declaration_training_field(form.definition_json or {}),
                    instruction_statuses=instruction_statuses,
                    validation_errors=[],
                    active_tab="basic",
                    trigger_descriptions=TRIGGER_DESCRIPTIONS,
                    **_workflow_editor_context(
                        (form.definition_json or {}).get("workflow") or {},
                        instruction_config=form.user_instruction_config,
                    ),
                ), 400
            form.user_instruction = instruction_config["description"] or None
            form.user_instruction_config = instruction_config
            form.slug = new_slug
            form.description = request.form.get("description", "").strip()
            form.is_active = request.form.get("is_active") == "on"
            form.is_public = request.form.get("is_public") == "on"
            form.label_text = request.form.get("label_text", "").strip()
            form.label_variant = request.form.get("label_variant", "").strip() or "project"
            form.label_color = request.form.get("label_color", "").strip() or "#b38d45"
            form.label_background = request.form.get("label_background", "").strip() or "#f7f3ec"
            logo_alignment = request.form.get("logo_alignment", "left").strip()
            form.logo_alignment = logo_alignment if logo_alignment in LOGO_ALIGNMENTS else "left"
            form.sort_order = parse_int(request.form.get("sort_order"), 0)
            current_app.extensions["services"].mail_settings_service.update_form(form, request.form)
            form.definition_json = updated_definition
            if g.admin_user.role == ROLE_SUPER_ADMIN and request.form.get("use_form_definition_json") == "on":
                sync_form_fields(db, form, updated_definition)
            selected_logo_id = parse_optional_int(request.form.get("logo_id"))
            if selected_logo_id and not can_select_logo(db, g.admin_user, selected_logo_id):
                abort(403)
            form.logo_id = selected_logo_id
            uploaded_regulation = request.files.get("regulation_file")
            if uploaded_regulation and uploaded_regulation.filename:
                try:
                    metadata = save_document_upload(
                        temp_dir=current_app.config["TEMP_DIR"],
                        uploaded_filename=uploaded_regulation.filename,
                        uploaded_bytes=uploaded_regulation.read(),
                        uploaded_mimetype=uploaded_regulation.mimetype,
                    )
                except UploadValidationError as exc:
                    flash(str(exc), "error")
                    assigned_user_ids = {permission.user_id for permission in form.permissions}
                    fields = active_fields_for_form(db, form.id)
                    return render_template(
                        "admin/forms/edit.html",
                        form=form,
                        fields=fields,
                        users=users,
                        assigned_user_ids=assigned_user_ids,
                        logos=logos,
                        training_field=get_declaration_training_field(form.definition_json or {}),
                        trigger_descriptions=TRIGGER_DESCRIPTIONS,
                        instruction_statuses=instruction_statuses,
                        validation_errors=[],
                        active_tab="documents",
                        **_workflow_editor_context(
                            (form.definition_json or {}).get("workflow") or {},
                            instruction_config=form.user_instruction_config,
                        ),
                    ), 400
                regulation = form.regulation or FormRegulation(form_id=form.id, original_filename="", storage_path="", mime_type="")
                update_form_regulation_from_upload(regulation, metadata, uploaded_by_user_id=g.admin_user.id)
                db.add(regulation)
            if g.admin_user.role == ROLE_SUPER_ADMIN:
                selected_user_ids = {int(item) for item in request.form.getlist("user_ids") if item.isdigit()}
                existing = {permission.user_id: permission for permission in form.permissions}
                for user in users:
                    if user.id in selected_user_ids and user.id not in existing:
                        db.add(FormPermission(user_id=user.id, form_id=form.id, can_manage=True))
                    if user.id not in selected_user_ids and user.id in existing:
                        db.delete(existing[user.id])
            db.commit()
            flash("Formularz został zapisany.", "success")
            return redirect(url_for("admin.form_edit", form_id=form.id, tab=active_tab))
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
            training_field=get_declaration_training_field(form.definition_json or {}),
            trigger_descriptions=TRIGGER_DESCRIPTIONS,
            instruction_statuses=instruction_statuses,
            validation_errors=[],
            active_tab=active_tab,
            **_workflow_editor_context(
                (form.definition_json or {}).get("workflow") or {},
                instruction_config=form.user_instruction_config,
            ),
        )


def _normalize_form_editor_tab(value: str | None, role: str) -> str:
    tab = str(value or "basic").strip().lower()
    if tab not in FORM_EDITOR_TABS:
        return "basic"
    if tab in {"permissions", "advanced"} and role != ROLE_SUPER_ADMIN:
        return "basic"
    return tab


def _tab_for_form_error(errors: list[str], fallback: str, form_data) -> str:
    text = " ".join(errors).lower()
    if (
        form_data.get("workflow_use_advanced_json") == "on"
        or form_data.get("use_form_definition_json") == "on"
    ) and ("json" in text or "konfigurac" in text):
        return "advanced"
    if "deklarac" in text:
        return "declaration"
    if "umow" in text or "contract" in text or "agreement" in text:
        return "agreement"
    if "szkol" in text or "training" in text:
        return "trainings"
    if "workflow" in text or "etap" in text or "status" in text or "decyzj" in text:
        return "workflow"
    if "mail" in text or "e-mail" in text or "smtp" in text:
        return "emails"
    if "regulamin" in text or "plik" in text:
        return "documents"
    if "slug" in text or "nazwa" in text or "tytu" in text:
        return "basic"
    return fallback


def _instruction_config_from_admin_form(
    form_data,
    *,
    existing: dict | None = None,
    workflow: dict | None = None,
    allow_advanced_json: bool = False,
) -> dict:
    workflow_builder_json = (
        form_data.get("workflow_json")
        if allow_advanced_json and form_data.get("workflow_use_advanced_json") == "on"
        else form_data.get("workflow_builder_json")
    )
    if workflow_builder_json:
        parsed_workflow = json.loads(workflow_builder_json)
        if not isinstance(parsed_workflow, dict):
            raise ValueError("Niepoprawna konfiguracja etapów workflow.")
        normalized_workflow = WorkflowConfigNormalizer().normalize(parsed_workflow)
        stages = [
            {
                "key": step["id"],
                "label": step["user_label"],
                "status_codes": [step["status"]],
                "description": step["description"],
                "next_action": step["next_action"],
                "final": step["final"],
                "rejected": step["rejected"],
                "sort_order": index,
            }
            for index, step in enumerate(normalized_workflow["steps"], start=1)
        ]
        return reconcile_instruction_config(
            existing,
            normalized_workflow,
            active_stages=stages,
            title=form_data.get("instruction_title", ""),
            description=form_data.get("user_instruction", ""),
        )
    raw_json = form_data.get("user_instruction_config")
    if raw_json is None:
        stages = (existing or {}).get("stages", [])
    else:
        parsed = json.loads(raw_json or "{}")
        if not isinstance(parsed, dict):
            raise ValueError("Niepoprawna konfiguracja instrukcji.")
        stages = parsed.get("stages", [])
    normalized = normalize_instruction_config(
        {"title": form_data.get("instruction_title", ""), "description": form_data.get("user_instruction", ""), "stages": stages}
    )
    return reconcile_instruction_config(normalized, workflow or {})


def _workflow_editor_context(
    workflow: dict,
    *,
    workflow_json: str | None = None,
    instruction_config: dict | None = None,
) -> dict:
    normalizer = WorkflowConfigNormalizer()
    normalized = normalizer.normalize(workflow)
    instruction_view = reconcile_instruction_config(instruction_config, normalized)
    instructions_by_key = {
        str(stage.get("key") or ""): stage
        for stage in instruction_view.get("stages", [])
        if stage.get("active", True)
    }
    instructions_by_status = {
        str(status): stage
        for stage in instruction_view.get("stages", [])
        if stage.get("active", True)
        for status in stage.get("status_codes", [])
    }
    for step in normalized.get("steps", []):
        instruction = instructions_by_key.get(str(step.get("id") or "")) or instructions_by_status.get(
            str(step.get("status") or "")
        )
        if instruction:
            step["description"] = instruction.get("description") or ""
            step["next_action"] = instruction.get("next_action") or ""
    instruction_view = reconcile_instruction_config(instruction_view, normalized)
    existing_statuses = [step.get("status") for step in normalized.get("steps", [])]
    for decision in normalized.get("decision_settings", []):
        existing_statuses.extend((decision.get("yes_status"), decision.get("no_status")))
    return {
        "workflow_builder": normalized,
        "workflow_json": workflow_json if workflow_json is not None else format_json(normalized),
        "workflow_statuses": workflow_status_options(existing_statuses),
        "workflow_advanced_elements": normalizer.advanced_elements(normalized),
        "instruction_config_view": instruction_view,
    }


@bp.post("/forms/<int:form_id>/workflow/repair-agreement-confirmation")
@login_required
def form_repair_agreement_confirmation(form_id: int):
    """Persist the focused repair exposed by the workflow editor."""
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        definition = dict(form.definition_json or {})
        workflow, changed = repair_agreement_confirmation_path(definition.get("workflow") or {})
        if not changed:
            flash("Ścieżka podpisu urzędu jest już poprawna albo potwierdzenie podpisu nie jest wymagane.", "info")
            return redirect(url_for("admin.form_edit", form_id=form.id, tab="workflow"))
        definition["workflow"] = WorkflowConfigNormalizer().normalize(workflow)
        form.definition_json = definition
        form.user_instruction_config = reconcile_instruction_config(
            form.user_instruction_config,
            definition["workflow"],
        )
        db.commit()
    flash("Naprawiono ścieżkę podpisu urzędu. Dodano lub uaktywniono wymagany etap.", "success")
    return redirect(url_for("admin.form_edit", form_id=form_id, tab="workflow"))


@bp.post("/forms/<int:form_id>/toggle")
@login_required
def form_toggle(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        form.is_active = not form.is_active
        db.commit()
        is_active = form.is_active
    flash("Formularz został aktywowany." if is_active else "Formularz został dezaktywowany.", "success")
    return redirect(url_for("admin.forms_list"))


@bp.post("/forms/<int:form_id>/training-selection/toggle")
@login_required
@role_required(ROLE_ADMIN, ROLE_SUPER_ADMIN)
def form_training_selection_toggle(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        form.training_selection_open = not form.training_selection_open
        db.commit()
        is_open = form.training_selection_open
    flash(
        "Nabór na szkolenia został otwarty."
        if is_open
        else "Nabór na szkolenia został zamknięty. Istniejące wybory pozostają bez zmian.",
        "success",
    )
    return redirect(url_for("admin.form_edit", form_id=form_id, tab="trainings"))


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
                    flash("Podaj nazwę pola.", "error")
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
                flash("Pole formularza zostało dodane.", "success")
                return redirect(url_for("admin.form_fields", form_id=form.id))
            if action.startswith("delete:"):
                field_id = parse_optional_int(action.split(":", 1)[1])
                field = db.get(FormField, field_id) if field_id else None
                if not field or field.form_id != form.id:
                    abort(404)
                field.active = False
                db.commit()
                flash("Pole zostało ukryte. Dane historyczne pozostają w zgłoszeniach.", "success")
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
            flash("Pola formularza zostały zapisane.", "success")
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
