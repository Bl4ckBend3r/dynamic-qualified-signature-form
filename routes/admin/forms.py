from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime
from io import BytesIO
from pathlib import Path

from flask import abort, current_app, flash, g, jsonify, redirect, render_template, request, send_file, url_for
from jinja2 import TemplateSyntaxError, UndefinedError, meta
from sqlalchemy import func, select

from services.form_access_service import generate_share_token

from form_loader import FIELD_STAGE_AFTER_ACCEPTANCE, FIELD_STAGE_INITIAL
from models import (
    AccessRole,
    Form,
    FormField,
    FormPermission,
    FormRegulation,
    FormSubmission,
    FormVersion,
    SubmissionTraining,
    User,
    VerificationChecklistDefinition,
    VerificationChecklistItemDefinition,
)
from services.verification_checklist_service import VerificationChecklistError
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
from services.form_builder_service import (
    FormBuilderError,
    LAYOUT_WIDTHS,
    apply_builder_state,
    serialize_builder_fields,
)
from services.field_availability_service import FieldAvailabilityService
from services.form_version_service import (
    FORM_VERSION_DRAFT,
    FormVersionError,
    FormVersionValidationError,
)
from services.documents.agreement_builder_service import (
    default_agreement_builder_document,
    normalize_agreement_builder_document,
    render_agreement_builder_template,
    validate_agreement_builder_document,
)
from services.documents.agreement_template_context_service import AgreementVariableCatalog, agreement_preview_context, agreement_variable_catalog
from services.documents.declaration_template_context_service import (
    DeclarationVariableCatalog,
    declaration_builder_variable_catalog,
    declaration_preview_context,
    declaration_variable_catalog,
)
from services.documents.document_builder_service import (
    default_document_builder_document,
    normalize_document_builder_document,
    render_document_builder_template,
    validate_document_builder_document,
)
from services.documents.docx_template_parser import DocxTemplateParseError
from services.process_instruction_service import (
    instruction_status_options,
    normalize_instruction_config,
    reconcile_instruction_config,
)
from services.site_document_service import (
    get_form_import_instruction,
    save_document_upload,
    update_form_regulation_from_upload,
)
from services.training_catalog_service import TrainingCatalogService
from services.training_service import parse_training_snapshots
from services.upload_validation import UploadValidationError
from services.workflow_config_service import (
    WorkflowConfigNormalizer,
    repair_agreement_confirmation_path,
    workflow_status_options,
)
from pdf_generator import render_document_html

from . import (
    ROLE_SUPER_ADMIN,
    active_fields_for_form,
    bp,
    can_manage_form,
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
    permission_required,
)
FORM_ACCESS_MODES = {
    "public",
    "unlisted",
    "disabled",
}


def apply_form_access_mode(form: Form, raw_mode: str | None) -> str:
    mode = str(raw_mode or "public").strip().lower()

    if mode not in FORM_ACCESS_MODES:
        mode = "public"

    if mode == "public":
        form.is_active = True
        form.is_public = True
        form.is_listed = True

    elif mode == "unlisted":
        form.is_active = True
        form.is_public = True
        form.is_listed = False

    elif mode == "disabled":
        form.is_active = False
        form.is_public = False
        form.is_listed = False

    return mode

FIELD_TYPES = [
    "text",
    "textarea",
    "email",
    "tel",
    "number",
    "date",
    "time",
    "select",
    "radio",
    "checkbox",
    "pesel",
    "file",
    "repeatable_group",
]
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


def _field_workflow_context(form: Form) -> tuple[dict, list[dict]]:
    definition = normalize_admin_form_definition(form.definition_json or {})
    return definition, FieldAvailabilityService().workflow_steps(definition)


def _availability_from_form(form_data, prefix: str, definition: dict, workflow_steps: list[dict], *, fallback_field=None) -> list[dict]:
    marker = f"{prefix}availability_present"
    if marker not in form_data:
        if fallback_field is not None and getattr(fallback_field, "availability_json", None):
            return list(fallback_field.availability_json or [])
        legacy_stage = str(form_data.get(f"{prefix}stage") or FIELD_STAGE_INITIAL)
        return FieldAvailabilityService().normalize_field(
            {"stage": legacy_stage, "required": form_data.get(f"{prefix}required") == "on"}, definition
        )["availability"]
    availability = []
    for index, step in enumerate(workflow_steps):
        step_id = str(step.get("id") or "")
        visible = form_data.get(f"{prefix}availability_{index}_visible") == "on"
        editable = form_data.get(f"{prefix}availability_{index}_editable") == "on"
        required = form_data.get(f"{prefix}availability_{index}_required") == "on"
        if required and (not visible or not editable):
            raise ValueError(f'Pole nie może być wymagane w etapie "{step_id}", jeśli nie jest widoczne i edytowalne.')
        availability.append({"step": step_id, "visible": visible, "editable": visible and editable, "required": required})
    return availability


def _set_field_availability(field: FormField, availability: list[dict], definition: dict) -> None:
    field.availability_json = availability
    initial = FieldAvailabilityService().initial_step(definition)
    initial_permission = next((item for item in availability if item.get("step") == initial), {})
    field.required = bool(initial_permission.get("required"))
    first_visible = next((str(item.get("step")) for item in availability if item.get("visible")), initial)
    field.stage = first_visible


def _editable_form_version(db, form: Form) -> FormVersion | None:
    service = current_app.extensions["services"].form_version_service
    if not service.has_versions(db, form.id):
        return None
    draft = service.editable_draft(db, form.id)
    if not draft:
        abort(409, description="Opublikowane i archiwalne wersje są tylko do odczytu. Najpierw utwórz wersję roboczą.")
    requested_version_id = request.form.get("form_version_id")
    if requested_version_id and str(draft.id) != str(requested_version_id):
        abort(409, description="Próba zapisu nieaktualnej albo nieedytowalnej wersji formularza.")
    return draft


def _sync_editable_form_version(db, form: Form) -> FormVersion | None:
    return current_app.extensions["services"].form_version_service.sync_draft_from_legacy(
        db,
        form,
        actor_id=g.admin_user.id,
    )


class AgreementPreviewError(ValueError):
    """A controlled, administrator-facing agreement preview failure."""

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        missing_variables: list[str] | tuple[str, ...] = (),
        errors: list[dict] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.missing_variables = sorted({str(name) for name in missing_variables if str(name).strip()})
        self.errors = list(errors or [])

    def as_payload(self) -> dict:
        payload = {
            "ok": False,
            "error": str(self),
            "reason": self.reason,
            "missing_variables": self.missing_variables,
        }
        if self.errors:
            payload["errors"] = self.errors
        return payload


@bp.get("/forms")
@login_required
def forms_list():
    user = g.admin_user
    with db_session_factory()() as db:
        forms = list_accessible_forms(db, user)
        available_forms = list(forms)
        counts = {
            slug: count
            for slug, count in db.execute(
                select(FormSubmission.form_slug, func.count(FormSubmission.id)).group_by(FormSubmission.form_slug)
            ).all()
        }
        name_filter = str(request.args.get("name") or "").strip().casefold()
        slug_filter = str(request.args.get("slug") or "").strip().casefold()
        status_filter = str(request.args.get("status") or "").strip()
        status_filter = status_filter if status_filter in {"active", "inactive"} else ""
        public_filter = str(request.args.get("public") or "").strip()
        public_filter = public_filter if public_filter in {"yes", "no"} else ""
        label_filter = str(request.args.get("label") or "").strip().casefold()
        date_from = _form_filter_date(request.args.get("date_from"))
        date_to = _form_filter_date(request.args.get("date_to"))
        forms = [
            form for form in forms
            if (not name_filter or name_filter in str(form.name or "").casefold())
            and (not slug_filter or slug_filter in str(form.slug or "").casefold())
            and (not status_filter or (form.is_active if status_filter == "active" else not form.is_active))
            and (not public_filter or (form.is_public if public_filter == "yes" else not form.is_public))
            and (not label_filter or label_filter in str(form.label_text or "").casefold())
            and (not date_from or (form.created_at and form.created_at.date() >= date_from))
            and (not date_to or (form.created_at and form.created_at.date() <= date_to))
        ]
        sort_field = request.args.get("sort") if request.args.get("sort") in {
            "name", "slug", "sort_order", "status", "public", "submission_count", "created_at"
        } else "sort_order"
        direction = request.args.get("direction") if request.args.get("direction") in {"asc", "desc"} else "asc"
        forms.sort(key=lambda form: _form_sort_value(form, sort_field, counts), reverse=direction == "desc")
        total = len(forms)
        try:
            page = max(1, int(request.args.get("page") or 1))
        except ValueError:
            page = 1
        per_page = 50
        pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, pages)
        forms = forms[(page - 1) * per_page : page * per_page]
        pagination = {"page": page, "pages": pages, "total": total, "has_previous": page > 1, "has_next": page < pages}
        pagination_urls = _form_pagination_urls(pagination)
        sort_urls = _form_sort_urls(sort_field, direction)
        sla_view_form_ids = set(
            current_app.extensions["services"].permission_service.form_ids_with_permission(
                db, user, "can_view_submissions"
            )
        )
        return render_template(
            "admin/forms/list.html",
            forms=forms,
            available_forms=available_forms,
            submission_counts=counts,
            filters=request.args,
            pagination=pagination,
            pagination_urls=pagination_urls,
            sort_urls=sort_urls,
            sla_view_form_ids=sla_view_form_ids,
        )


@bp.get("/forms/<int:form_id>/versions")
@login_required
def form_versions(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id)
        service = current_app.extensions["services"].form_version_service
        versions = service.list_versions(db, form.id)
        diffs = {
            version.id: service.simple_diff(
                version.source_version.definition_json if version.source_version else {},
                version.definition_json,
            )
            for version in versions
        }
        return render_template(
            "admin/forms/versions.html",
            form=form,
            versions=versions,
            diffs=diffs,
            can_manage=can_manage_form(db, g.admin_user, form.id),
        )


@bp.route("/forms/<int:form_id>/checklists", methods=["GET", "POST"])
@login_required
def form_checklists(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=request.method == "POST")
        version_service = current_app.extensions["services"].form_version_service
        checklist_service = current_app.extensions["services"].verification_checklist_service
        draft = version_service.editable_draft(db, form.id)
        if request.method == "POST":
            if not draft:
                flash("Najpierw utwórz wersję roboczą formularza.", "error")
                return redirect(url_for("admin.form_versions", form_id=form.id))
            action = str(request.form.get("action") or "").strip()
            try:
                if action == "create_checklist":
                    checklist_service.create_checklist(
                        db, draft, name=request.form.get("name", ""),
                        workflow_step=request.form.get("workflow_step", ""),
                        position=parse_int(request.form.get("position")),
                    )
                elif action in {"update_checklist", "delete_checklist", "add_item"}:
                    checklist = db.get(VerificationChecklistDefinition, parse_int(request.form.get("checklist_id"))) or abort(404)
                    if checklist.form_version_id != draft.id:
                        abort(404)
                    if action == "delete_checklist":
                        checklist_service.delete_checklist(db, checklist)
                    elif action == "update_checklist":
                        checklist.name = str(request.form.get("name") or "").strip() or checklist.name
                        step = str(request.form.get("workflow_step") or "").strip()
                        checklist_service._require_workflow_step(draft, step)
                        checklist.workflow_step = step
                        checklist.position = parse_int(request.form.get("position"))
                        checklist.active = request.form.get("active") == "on"
                    else:
                        checklist_service.add_item(
                            db, checklist, key=request.form.get("key"), label=request.form.get("label"),
                            description=request.form.get("description"), position=parse_int(request.form.get("position")),
                            blocking=request.form.get("blocking") == "on", required=request.form.get("required") == "on",
                            document_required=request.form.get("document_required") == "on",
                            allow_not_applicable=request.form.get("allow_not_applicable") == "on",
                            sensitive=request.form.get("sensitive") == "on",
                        )
                elif action in {"update_item", "delete_item"}:
                    item = db.get(VerificationChecklistItemDefinition, parse_int(request.form.get("item_id"))) or abort(404)
                    if item.checklist.form_version_id != draft.id:
                        abort(404)
                    if action == "delete_item":
                        checklist_service.delete_item(db, item)
                    else:
                        item.label = str(request.form.get("label") or "").strip() or item.label
                        item.description = str(request.form.get("description") or "").strip()
                        item.position = parse_int(request.form.get("position"))
                        item.blocking = request.form.get("blocking") == "on"
                        item.required = request.form.get("required") == "on"
                        item.document_required = request.form.get("document_required") == "on"
                        item.allow_not_applicable = request.form.get("allow_not_applicable") == "on"
                        item.sensitive = request.form.get("sensitive") == "on"
                else:
                    abort(400)
                db.commit()
                flash("Zapisano konfigurację checklisty.", "success")
            except VerificationChecklistError as exc:
                db.rollback()
                flash(str(exc), "error")
            return redirect(url_for("admin.form_checklists", form_id=form.id))

        version = draft or version_service.resolve_published(db, form.id)
        checklists = checklist_service.list_for_version(db, version.id) if version else []
        steps = (((version.definition_json or {}).get("workflow") or {}).get("steps") or []) if version else []
        return render_template(
            "admin/forms/checklists.html", form=form, version=version, draft=draft,
            checklists=checklists, workflow_steps=steps,
            can_manage=can_manage_form(db, g.admin_user, form.id),
        )


@bp.get("/forms/<int:form_id>/versions/<int:version_id>/preview")
@login_required
def form_version_preview(form_id: int, version_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id)
        version = db.get(FormVersion, version_id) or abort(404)
        if version.form_id != form.id:
            abort(404)
        definition = normalize_admin_form_definition(deepcopy(version.definition_json or {}))
        return render_template(
            "admin/forms/version_preview.html",
            form=form,
            version=version,
            definition=definition,
        )


@bp.post("/forms/<int:form_id>/versions/<int:version_id>/clone")
@login_required
def form_version_clone(form_id: int, version_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        version = db.get(FormVersion, version_id) or abort(404)
        service = current_app.extensions["services"].form_version_service
        try:
            draft = service.clone_to_draft(
                db,
                form,
                version,
                actor_id=g.admin_user.id,
                bump=str(request.form.get("bump") or "minor"),
            )
            draft_label = draft.version_label
            db.commit()
        except FormVersionError as exc:
            db.rollback()
            flash(str(exc), "error")
            return redirect(url_for("admin.form_versions", form_id=form.id))
    flash(f"Utworzono wersję roboczą {draft_label}.", "success")
    return redirect(url_for("admin.form_edit", form_id=form_id))


@bp.post("/forms/<int:form_id>/versions/<int:version_id>/publish")
@login_required
def form_version_publish(form_id: int, version_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        version = db.get(FormVersion, version_id) or abort(404)
        service = current_app.extensions["services"].form_version_service
        try:
            if version.form_id != form.id or version.status != FORM_VERSION_DRAFT:
                raise FormVersionError("Można opublikować wyłącznie wersję roboczą tego formularza.")
            service.publish(
                db,
                form,
                version,
                actor_id=g.admin_user.id,
                change_summary=str(request.form.get("change_summary") or "").strip(),
            )
            published_label = version.version_label
            db.commit()
        except FormVersionValidationError as exc:
            db.rollback()
            for error in exc.errors:
                flash(error, "error")
            return redirect(url_for("admin.form_versions", form_id=form.id))
        except FormVersionError as exc:
            db.rollback()
            flash(str(exc), "error")
            return redirect(url_for("admin.form_versions", form_id=form.id))
    flash(f"Opublikowano wersję {published_label}.", "success")
    return redirect(url_for("admin.form_versions", form_id=form_id))


@bp.post("/forms/<int:form_id>/versions/<int:version_id>/archive")
@login_required
def form_version_archive(form_id: int, version_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        version = db.get(FormVersion, version_id) or abort(404)
        try:
            if version.form_id != form.id:
                abort(404)
            current_app.extensions["services"].form_version_service.archive(db, version)
            db.commit()
        except FormVersionError as exc:
            db.rollback()
            flash(str(exc), "error")
    return redirect(url_for("admin.form_versions", form_id=form_id))


@bp.post("/forms/<int:form_id>/versions/<int:version_id>/delete")
@login_required
def form_version_delete(form_id: int, version_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        version = db.get(FormVersion, version_id) or abort(404)
        try:
            if version.form_id != form.id:
                abort(404)
            current_app.extensions["services"].form_version_service.delete_draft(db, version)
            db.commit()
            flash("Usunięto wersję roboczą.", "success")
        except FormVersionError as exc:
            db.rollback()
            flash(str(exc), "error")
    return redirect(url_for("admin.form_versions", form_id=form_id))


def _form_filter_date(value):
    try:
        return datetime.fromisoformat(str(value or "").strip()).date() if value else None
    except ValueError:
        return None


def _form_sort_value(form, field: str, counts: dict[str, int]):
    if field == "status":
        return int(bool(form.is_active))
    if field == "public":
        return int(bool(form.is_public))
    if field == "submission_count":
        return counts.get(form.slug, 0)
    value = getattr(form, field, "")
    if field == "created_at":
        return value or datetime.min
    if field in {"name", "slug"}:
        return str(value or "").casefold()
    return value or 0


def _form_pagination_urls(pagination: dict) -> dict[str, str]:
    values = request.args.to_dict(flat=True)
    result = {"previous": "", "next": ""}
    if pagination["has_previous"]:
        result["previous"] = url_for("admin.forms_list", **{**values, "page": pagination["page"] - 1})
    if pagination["has_next"]:
        result["next"] = url_for("admin.forms_list", **{**values, "page": pagination["page"] + 1})
    return result


def _form_sort_urls(current_sort: str, current_direction: str) -> dict[str, str]:
    result = {}
    for field in ("name", "slug", "sort_order", "status", "public", "submission_count", "created_at"):
        values = request.args.to_dict(flat=True)
        values.pop("page", None)
        values["sort"] = field
        values["direction"] = "desc" if current_sort == field and current_direction == "asc" else "asc"
        result[field] = url_for("admin.forms_list", **values)
    return result


@bp.post("/forms/<int:form_id>/delete")
@login_required
@permission_required("can_manage_site")
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
@permission_required("can_manage_site")
def forms_upload():
    if request.method == "GET":
        with db_session_factory()() as db:
            instruction = get_form_import_instruction(db)
            logos = list_selectable_logos(db, g.admin_user, None)
            return render_template("admin/forms/upload.html", import_instruction=instruction, logos=logos)

    uploaded_file = request.files.get("form_file")
    if not uploaded_file or not uploaded_file.filename:
        flash("Wybierz plik formularza.", "error")
        return _render_forms_upload_error("fields"), 400
    suffix = Path(uploaded_file.filename).suffix.lower()
    if suffix not in {".json", ".html", ".docx"}:
        flash("Dozwolone formaty to JSON, HTML i DOCX.", "error")
        return _render_forms_upload_error("fields"), 400
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
        return _render_forms_upload_error("fields"), 400

    slug = request.form.get("slug", "").strip() or form_definition.get("slug") or Path(uploaded_file.filename).stem
    slug = normalize_slug(slug)
    name = request.form.get("name", "").strip() or form_definition.get("name") or form_definition.get("title") or slug
    title = request.form.get("title", "").strip() or form_definition.get("title") or name
    description = request.form.get("description", "").strip() or str(form_definition.get("description") or "").strip()
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
            return _render_forms_upload_error("basic"), 400
        selected_logo_id = parse_optional_int(request.form.get("logo_id"))
        if selected_logo_id and not can_select_logo(db, g.admin_user, selected_logo_id):
            abort(403)
        logo_alignment = request.form.get("logo_alignment", "left").strip()
        form = Form(
            slug=slug,
            name=name,
            title=title,
            description=description,
            user_instruction=user_instruction,
            user_instruction_config=instruction_config,
            definition_json=form_definition,
            created_by_id=g.admin_user.id,
            is_active=is_active,
            is_public=is_public,
            label_text=(
                request.form.get("label_text", "").strip()
                or str(form_definition.get("label_text") or form_definition.get("project_label") or "").strip()
            ),
            label_color=(
                request.form.get("label_color", "").strip()
                or str(form_definition.get("label_color") or "").strip()
                or "#b38d45"
            ),
            label_background=(
                request.form.get("label_background", "").strip()
                or str(form_definition.get("label_background") or "").strip()
                or "#f7f3ec"
            ),
            sort_order=parse_int(request.form.get("sort_order"), 0),
            logo_id=selected_logo_id,
            logo_alignment=logo_alignment if logo_alignment in LOGO_ALIGNMENTS else "left",
        )
        current_app.extensions["services"].mail_settings_service.update_form(form, request.form)
        db.add(form)
        db.flush()
        sync_form_fields(db, form, form_definition)
        current_app.extensions["services"].form_version_service.create_initial_version(
            db,
            form,
            actor_id=g.admin_user.id,
            status=FORM_VERSION_DRAFT,
        )
        db.add(FormPermission(user_id=g.admin_user.id, form_id=form.id, can_manage=True))
        if g.admin_user.role != ROLE_SUPER_ADMIN:
            administrator_role = db.execute(select(AccessRole).where(
                AccessRole.key == "form_administrator", AccessRole.scope == "form", AccessRole.is_active.is_(True)
            )).scalar_one_or_none()
            if not administrator_role:
                raise RuntimeError("Brak systemowej roli administratora formularza. Uruchom migracje bazy.")
            current_app.extensions["services"].permission_service.replace_form_roles(
                db, target=g.admin_user, form=form, role_ids=[administrator_role.id], actor=g.admin_user
            )
        db.commit()
        form_id = form.id
    flash("Formularz został wgrany, a pola zostały wykryte.", "success")
    return redirect(url_for("admin.form_fields", form_id=form_id))


def _render_forms_upload_error(active_tab: str):
    with db_session_factory()() as db:
        return render_template(
            "admin/forms/upload.html",
            import_instruction=get_form_import_instruction(db),
            logos=list_selectable_logos(db, g.admin_user, None),
            upload_active_tab=active_tab,
        )


@bp.route("/forms/<int:form_id>/edit", methods=["GET", "POST"])
@login_required
def form_edit(form_id: int):
    with db_session_factory()() as db:
        requested_tab = request.form.get("active_tab") if request.method == "POST" else request.args.get("tab")
        active_tab = _normalize_form_editor_tab(requested_tab, g.admin_user.role)
        required_permission = "can_edit_workflow" if active_tab in {"workflow", "instructions"} else "can_edit_form"
        form = ensure_form_access(db, form_id, permission=required_permission)
        version_service = current_app.extensions["services"].form_version_service
        editable_version = version_service.editable_draft(db, form.id)
        if version_service.has_versions(db, form.id) and not editable_version:
            flash("Opublikowana konfiguracja jest niezmienna. Utwórz z niej nową wersję roboczą.", "info")
            return redirect(url_for("admin.form_versions", form_id=form.id))
        form.editable_version_id = editable_version.id if editable_version else None
        form.workflow_ids_locked = bool(
            db.execute(select(func.count(FormSubmission.id)).where(FormSubmission.form_slug == form.slug)).scalar()
        )
        users = db.execute(select(User).order_by(User.email)).scalars().all()
        logos = list_selectable_logos(db, g.admin_user, form.logo_id)
        instruction_statuses = instruction_status_options()
        if request.method == "POST":
            _editable_form_version(db, form)
            training_catalog_actions: list[dict] = []
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
                updated_workflow = updated_definition.get("workflow") or {}
                if updated_workflow.get("requires_declaration") and updated_workflow.get("declaration_template_source") == "builder":
                    declaration_builder_errors = validate_document_builder_document(
                        updated_workflow.get("declaration_builder_document"),
                        active_fields_for_form(db, form.id),
                        "declaration",
                        form_definition=updated_definition,
                    )
                    if declaration_builder_errors:
                        raise ValueError("Nie można aktywować szablonu deklaracji. " + " ".join(error.message for error in declaration_builder_errors))
                if updated_workflow.get("requires_contract") and updated_workflow.get("contract_template_source") == "builder":
                    builder_errors = validate_agreement_builder_document(
                        updated_workflow.get("contract_builder_document"),
                        active_fields_for_form(db, form.id),
                    )
                    if builder_errors:
                        raise ValueError("Nie można aktywować szablonu. " + " ".join(error.message for error in builder_errors))
                used_training_ids = _used_training_ids_for_form(db, form.slug)
                removed_ids = request.form.getlist("training_removed_id")
                removed_reasons = request.form.getlist(
                    "training_removed_reason"
                )
                reason_by_id = {
                    str(training_id): str(reason).strip()
                    for training_id, reason in zip(
                        request.form.getlist("training_item_id"),
                        request.form.getlist("training_item_change_reason"),
                    )
                    if str(training_id).strip() and str(reason).strip()
                }
                reason_by_id.update({
                    str(training_id): str(
                        removed_reasons[index]
                        if index < len(removed_reasons)
                        else ""
                    ).strip()
                    for index, training_id in enumerate(removed_ids)
                    if str(training_id).strip()
                })
                (
                    updated_definition,
                    training_catalog_actions,
                ) = TrainingCatalogService().reconcile_definition(
                    form.definition_json or {},
                    updated_definition,
                    used_training_ids=used_training_ids,
                    removal_reasons=reason_by_id,
                    actor_id=g.admin_user.id,
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
            apply_form_access_mode(
                form,
                request.form.get("access_mode"),
            )
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
                db.flush()
                current_app.extensions["services"].compliance_service.stage_regulation_version(
                    db,
                    form,
                    regulation,
                    actor_id=g.admin_user.id,
                )
            _sync_editable_form_version(db, form)
            db.commit()
            _audit_training_catalog_actions(
                form.slug,
                training_catalog_actions,
            )
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
            **_agreement_docx_editor_context(form, fields),
            **_declaration_editor_context(form, fields),
        )


@bp.post("/forms/<int:form_id>/declaration-template/docx")
@login_required
def declaration_docx_template_upload(form_id: int):
    uploaded = request.files.get("declaration_docx_template")
    if not uploaded or not uploaded.filename:
        flash("Wybierz plik DOCX z szablonem deklaracji.", "error")
        return redirect(url_for("admin.form_edit", form_id=form_id, tab="declaration"))
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        editable_version = _editable_form_version(db, form)
        fields = active_fields_for_form(db, form.id)
        definition = deepcopy(form.definition_json or {})
        try:
            metadata = current_app.extensions["services"].agreement_docx_template_service.upload(
                form_slug=form.slug,
                filename=uploaded.filename,
                content=uploaded.read(),
                fields=fields,
                uploaded_by_user_id=g.admin_user.id,
                document_type="declaration",
                form_definition=definition,
                version_key=f"{editable_version.version_major}-{editable_version.version_minor}-{editable_version.id}" if editable_version else None,
            )
        except (DocxTemplateParseError, ValueError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("admin.form_edit", form_id=form.id, tab="declaration"))
        workflow = dict(definition.get("workflow") or {})
        imported_builder = metadata.pop("builder_document", None)
        workflow["declaration_docx_template"] = metadata
        if imported_builder:
            workflow["declaration_builder_document"] = normalize_document_builder_document(imported_builder, "declaration")
        workflow["declaration_template_source"] = "docx"
        workflow["managed_documents"] = True
        definition["workflow"] = workflow
        form.definition_json = definition
        _sync_editable_form_version(db, form)
        db.commit()
        if metadata["unknown_variables"]:
            flash("DOCX zapisano, ale wymaga poprawy nieznanych zmiennych: " + ", ".join(metadata["unknown_variables"]), "warning")
        else:
            flash("Szablon DOCX zapisano i ustawiono jako aktywne źródło deklaracji.", "success")
        return redirect(url_for("admin.form_edit", form_id=form.id, tab="declaration"))


@bp.post("/forms/<int:form_id>/declaration-template/builder")
@login_required
def declaration_builder_save(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        editable_version = _editable_form_version(db, form)
        fields = active_fields_for_form(db, form.id)
        try:
            document = _builder_document_from_request("declaration")
        except ValueError as exc:
            return jsonify({"ok": False, "errors": [{"path": "blocks", "message": str(exc)}]}), 422
        errors = validate_document_builder_document(
            document,
            fields,
            "declaration",
            form_definition=form.definition_json or {},
        )
        if errors:
            return jsonify({"ok": False, "errors": [error.as_dict() for error in errors]}), 422
        action = str(request.form.get("action") or "draft").strip().casefold()
        definition = deepcopy(form.definition_json or {})
        workflow = dict(definition.get("workflow") or {})
        if workflow.get("declaration_template_source") == "builder" and not workflow.get("declaration_builder_active_document"):
            workflow["declaration_builder_active_document"] = normalize_document_builder_document(
                workflow.get("declaration_builder_document") or default_document_builder_document("declaration"),
                "declaration",
            )
        workflow["declaration_builder_document"] = document
        workflow["declaration_builder_updated_at"] = datetime.now().astimezone().isoformat()
        workflow["declaration_builder_updated_by"] = g.admin_user.id
        workflow["declaration_builder_status"] = "active" if action == "activate" else "draft"
        if action == "activate":
            workflow["declaration_builder_active_document"] = document
            workflow["declaration_template_source"] = "builder"
            workflow["declaration_template_updated_at"] = workflow["declaration_builder_updated_at"]
            workflow["declaration_template_updated_by"] = g.admin_user.id
            workflow["declaration_template_updated_source"] = "builder"
            workflow["managed_documents"] = True
        definition["workflow"] = workflow
        form.definition_json = definition
        _sync_editable_form_version(db, form)
        db.commit()
        return jsonify({
            "ok": True,
            "status": workflow["declaration_builder_status"],
            "updated_at": workflow["declaration_builder_updated_at"],
            "message": "Kreator zapisano jako aktywny szablon." if action == "activate" else "Wersja robocza kreatora została zapisana.",
        })


@bp.get("/forms/<int:form_id>/documents/declaration/builder")
@login_required
def declaration_builder_view(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        fields = active_fields_for_form(db, form.id)
        form.definition_json = normalize_admin_form_definition(form.definition_json or {})
        workflow = (form.definition_json or {}).get("workflow") or {}
        context = _declaration_editor_context(form, fields)
        return render_template(
            "admin/documents/document_builder.html",
            form=form,
            workflow=workflow,
            document_builder_standalone=True,
            document_builder_type="declaration",
            document_builder_title="Deklaracja",
            document_builder_description="Jedna deklaracja odpowiada jednemu zgłoszeniu i nie zawiera danych szkoleń.",
            document_builder_document=context["declaration_builder_document"],
            document_builder_updated_at=context["declaration_builder_updated_at"],
            document_builder_is_new=context["declaration_builder_is_new"],
            document_builder_variables=context["declaration_builder_variables"],
            document_builder_submissions=context["declaration_preview_submissions"],
            document_builder_can_show_html=context["declaration_builder_can_show_html"],
            document_docx_metadata=context["declaration_docx_metadata"],
            document_builder_save_url=url_for("admin.declaration_builder_save", form_id=form.id),
            document_builder_preview_url=url_for("admin.declaration_template_preview", form_id=form.id),
            document_builder_pdf_url=url_for("admin.declaration_template_example_pdf", form_id=form.id),
            document_builder_import_url=url_for("admin.declaration_docx_import_builder", form_id=form.id),
        )


@bp.post("/forms/<int:form_id>/declaration-template/docx/import-builder")
@login_required
def declaration_docx_import_builder(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        editable_version = _editable_form_version(db, form)
        definition = deepcopy(form.definition_json or {})
        workflow = dict(definition.get("workflow") or {})
        metadata = workflow.get("declaration_docx_template") or {}
        try:
            parsed = current_app.extensions["services"].agreement_docx_template_service.parse_stored_template(metadata)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("admin.form_edit", form_id=form.id, tab="declaration"))
        document = normalize_document_builder_document(parsed.builder_document or {}, "declaration")
        if not document.get("blocks"):
            flash("Tego szablonu DOCX nie można przekonwertować do kreatora.", "error")
            return redirect(url_for("admin.form_edit", form_id=form.id, tab="declaration"))
        errors = validate_document_builder_document(
            document,
            active_fields_for_form(db, form.id),
            "declaration",
            form_definition=definition,
        )
        if errors:
            flash("Import DOCX wymaga poprawy: " + " ".join(error.message for error in errors), "warning")
        workflow["declaration_builder_document"] = document
        workflow["declaration_builder_status"] = "draft"
        workflow["declaration_builder_updated_at"] = datetime.now().astimezone().isoformat()
        workflow["declaration_builder_updated_by"] = g.admin_user.id
        definition["workflow"] = workflow
        form.definition_json = definition
        _sync_editable_form_version(db, form)
        db.commit()
        flash("Szablon Word zaimportowano do wersji roboczej kreatora deklaracji.", "success")
        return redirect(url_for("admin.form_edit", form_id=form.id, tab="declaration"))


@bp.get("/forms/<int:form_id>/declaration-template/docx")
@login_required
def declaration_docx_template_download(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        metadata = ((form.definition_json or {}).get("workflow") or {}).get("declaration_docx_template") or {}
        try:
            content = current_app.extensions["services"].agreement_docx_template_service.download(metadata)
        except Exception:
            abort(404)
        return send_file(BytesIO(content), mimetype=metadata.get("mime_type"), as_attachment=True, download_name=metadata.get("original_filename") or "szablon-deklaracji.docx")


@bp.post("/forms/<int:form_id>/declaration-template/docx/delete")
@login_required
def declaration_docx_template_delete(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        editable_version = _editable_form_version(db, form)
        definition = deepcopy(form.definition_json or {})
        workflow = dict(definition.get("workflow") or {})
        if editable_version is None:
            current_app.extensions["services"].agreement_docx_template_service.delete(workflow.get("declaration_docx_template") or {})
        workflow.pop("declaration_docx_template", None)
        workflow["declaration_template_source"] = "builder" if workflow.get("declaration_builder_active_document") else "html"
        definition["workflow"] = workflow
        form.definition_json = definition
        _sync_editable_form_version(db, form)
        db.commit()
        flash("Szablon DOCX deklaracji został usunięty.", "success")
        return redirect(url_for("admin.form_edit", form_id=form.id, tab="declaration"))


@bp.get("/forms/<int:form_id>/declaration-template/docx/sample")
@bp.get("/forms/<int:form_id>/documents/declaration/example.docx")
@login_required
def declaration_docx_template_sample(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        content = current_app.extensions["services"].agreement_docx_template_service.sample_docx(
            active_fields_for_form(db, form.id), document_type="declaration"
        )
        return send_file(BytesIO(content), mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document", as_attachment=True, download_name="przykladowy-szablon-deklaracji.docx")


@bp.route("/forms/<int:form_id>/documents/declaration/preview", methods=["GET", "POST"])
@login_required
def declaration_template_preview(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        fields = active_fields_for_form(db, form.id)
        try:
            template_html, context = _declaration_preview_material(form, fields)
            return jsonify({"ok": True, "html": render_document_html(current_app._get_current_object(), template_html, context)})
        except AgreementPreviewError as exc:
            return _document_preview_error_response(form, "declaration", exc)
        except UndefinedError as exc:
            missing = _undefined_variable_name(exc)
            return _document_preview_error_response(form, "declaration", AgreementPreviewError(_missing_variables_message([missing] if missing else []), reason="missing_context_variables", missing_variables=[missing] if missing else []))


@bp.route("/forms/<int:form_id>/documents/declaration/example.pdf", methods=["GET", "POST"])
@login_required
def declaration_template_example_pdf(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        fields = active_fields_for_form(db, form.id)
        try:
            template_html, context = _declaration_preview_material(form, fields)
            pdf = current_app.extensions["services"].document_service.pdf_render_service.render_document_pdf_bytes(
                app=current_app._get_current_object(), template_name="declaration_template.html", template_html=template_html, context=context
            )
        except AgreementPreviewError as exc:
            return _document_preview_error_response(form, "declaration", exc)
        return send_file(BytesIO(pdf), mimetype="application/pdf", as_attachment=True, download_name=f"przykladowa_deklaracja_{form.slug}.pdf")


@bp.post("/forms/<int:form_id>/agreement-template/docx")
@login_required
def agreement_docx_template_upload(form_id: int):
    uploaded = request.files.get("agreement_docx_template")
    if not uploaded or not uploaded.filename:
        flash("Wybierz plik DOCX z szablonem umowy.", "error")
        return redirect(url_for("admin.form_edit", form_id=form_id, tab="agreement"))
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        editable_version = _editable_form_version(db, form)
        fields = active_fields_for_form(db, form.id)
        try:
            metadata = current_app.extensions["services"].agreement_docx_template_service.upload(
                form_slug=form.slug,
                filename=uploaded.filename,
                content=uploaded.read(),
                fields=fields,
                uploaded_by_user_id=g.admin_user.id,
                version_key=f"{editable_version.version_major}-{editable_version.version_minor}-{editable_version.id}" if editable_version else None,
            )
        except (DocxTemplateParseError, ValueError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("admin.form_edit", form_id=form.id, tab="agreement"))
        definition = deepcopy(form.definition_json or {})
        workflow = dict(definition.get("workflow") or {})
        imported_builder = metadata.pop("builder_document", None)
        workflow["contract_docx_template"] = metadata
        if imported_builder:
            workflow["contract_builder_document"] = imported_builder
        workflow["contract_template_source"] = "docx"
        workflow["managed_documents"] = True
        definition["workflow"] = workflow
        form.definition_json = definition
        _sync_editable_form_version(db, form)
        db.commit()
        if metadata["unknown_variables"]:
            flash("DOCX zapisano, ale wymaga poprawy nieznanych zmiennych: " + ", ".join(metadata["unknown_variables"]), "warning")
        else:
            flash("Szablon DOCX zapisano i ustawiono jako aktywne źródło umowy.", "success")
        return redirect(url_for("admin.form_edit", form_id=form.id, tab="agreement"))


@bp.post("/forms/<int:form_id>/agreement-template/builder")
@login_required
def agreement_builder_save(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        _editable_form_version(db, form)
        fields = active_fields_for_form(db, form.id)
        try:
            document = _builder_document_from_request()
        except ValueError as exc:
            return jsonify({"ok": False, "errors": [{"path": "blocks", "message": str(exc)}]}), 422
        errors = validate_agreement_builder_document(document, fields)
        if errors:
            return jsonify({"ok": False, "errors": [error.as_dict() for error in errors]}), 422

        action = str(request.form.get("action") or "draft").strip().casefold()
        definition = deepcopy(form.definition_json or {})
        workflow = dict(definition.get("workflow") or {})
        if workflow.get("contract_template_source") == "builder" and not workflow.get("contract_builder_active_document"):
            workflow["contract_builder_active_document"] = normalize_agreement_builder_document(
                workflow.get("contract_builder_document") or default_agreement_builder_document()
            )
        workflow["contract_builder_document"] = document
        workflow["contract_builder_updated_at"] = datetime.now().astimezone().isoformat()
        workflow["contract_builder_updated_by"] = g.admin_user.id
        workflow["contract_builder_status"] = "active" if action == "activate" else "draft"
        if action == "activate":
            workflow["contract_builder_active_document"] = document
            workflow["contract_template_source"] = "builder"
            workflow["contract_template_updated_at"] = workflow["contract_builder_updated_at"]
            workflow["contract_template_updated_by"] = g.admin_user.id
            workflow["contract_template_updated_source"] = "builder"
            workflow["managed_documents"] = True
        definition["workflow"] = workflow
        form.definition_json = definition
        _sync_editable_form_version(db, form)
        db.commit()
        return jsonify({
            "ok": True,
            "status": workflow["contract_builder_status"],
            "updated_at": workflow["contract_builder_updated_at"],
            "message": "Kreator zapisano jako aktywny szablon." if action == "activate" else "Wersja robocza kreatora została zapisana.",
        })


@bp.get("/forms/<int:form_id>/documents/agreement/builder")
@login_required
def agreement_builder_view(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        fields = active_fields_for_form(db, form.id)
        form.definition_json = normalize_admin_form_definition(form.definition_json or {})
        workflow = (form.definition_json or {}).get("workflow") or {}
        return render_template(
            "admin/documents/agreement_builder.html",
            form=form,
            workflow=workflow,
            agreement_builder_standalone=True,
            **_agreement_docx_editor_context(form, fields),
        )


@bp.post("/forms/<int:form_id>/agreement-template/docx/import-builder")
@login_required
def agreement_docx_import_builder(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        _editable_form_version(db, form)
        definition = deepcopy(form.definition_json or {})
        workflow = dict(definition.get("workflow") or {})
        metadata = workflow.get("contract_docx_template") or {}
        try:
            parsed = current_app.extensions["services"].agreement_docx_template_service.parse_stored_template(metadata)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("admin.form_edit", form_id=form.id, tab="agreement"))
        document = parsed.builder_document
        if not document:
            flash("Tego szablonu DOCX nie można przekonwertować do kreatora.", "error")
            return redirect(url_for("admin.form_edit", form_id=form.id, tab="agreement"))
        errors = validate_agreement_builder_document(document, active_fields_for_form(db, form.id))
        if errors:
            flash("Import DOCX wymaga poprawy: " + " ".join(error.message for error in errors), "warning")
        if workflow.get("contract_template_source") == "builder" and not workflow.get("contract_builder_active_document"):
            workflow["contract_builder_active_document"] = normalize_agreement_builder_document(
                workflow.get("contract_builder_document") or default_agreement_builder_document()
            )
        workflow["contract_builder_document"] = document
        workflow["contract_builder_status"] = "draft"
        workflow["contract_builder_updated_at"] = datetime.now().astimezone().isoformat()
        workflow["contract_builder_updated_by"] = g.admin_user.id
        definition["workflow"] = workflow
        form.definition_json = definition
        _sync_editable_form_version(db, form)
        db.commit()
        flash("Szablon Word zaimportowano do wersji roboczej kreatora.", "success")
        return redirect(url_for("admin.form_edit", form_id=form.id, tab="agreement"))


@bp.get("/forms/<int:form_id>/agreement-template/docx")
@login_required
def agreement_docx_template_download(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        metadata = ((form.definition_json or {}).get("workflow") or {}).get("contract_docx_template") or {}
        try:
            content = current_app.extensions["services"].agreement_docx_template_service.download(metadata)
        except Exception:
            current_app.logger.warning("Nie udało się pobrać szablonu DOCX formularza %s.", form.slug, exc_info=True)
            abort(404)
        return send_file(BytesIO(content), mimetype=metadata.get("mime_type"), as_attachment=True, download_name=metadata.get("original_filename") or "szablon-umowy.docx")


@bp.post("/forms/<int:form_id>/agreement-template/docx/delete")
@login_required
def agreement_docx_template_delete(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        editable_version = _editable_form_version(db, form)
        definition = deepcopy(form.definition_json or {})
        workflow = dict(definition.get("workflow") or {})
        metadata = workflow.get("contract_docx_template") or {}
        if editable_version is None:
            current_app.extensions["services"].agreement_docx_template_service.delete(metadata)
        workflow.pop("contract_docx_template", None)
        workflow["contract_template_source"] = "builder" if workflow.get("contract_builder_active_document") else "html"
        definition["workflow"] = workflow
        form.definition_json = definition
        _sync_editable_form_version(db, form)
        db.commit()
        flash("Szablon DOCX został usunięty. Źródłem umowy jest ponownie HTML.", "success")
        return redirect(url_for("admin.form_edit", form_id=form.id, tab="agreement"))


@bp.get("/forms/<int:form_id>/agreement-template/docx/sample")
@bp.get("/forms/<int:form_id>/documents/agreement/example.docx")
@login_required
def agreement_docx_template_sample(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        content = current_app.extensions["services"].agreement_docx_template_service.sample_docx(active_fields_for_form(db, form.id))
        return send_file(BytesIO(content), mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document", as_attachment=True, download_name="przykladowy-szablon-umowy.docx")


@bp.route("/forms/<int:form_id>/documents/agreement/preview", methods=["GET", "POST"])
@login_required
def agreement_template_preview(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        fields = active_fields_for_form(db, form.id)
        request_values = request.form if request.method == "POST" else request.args
        mode = str(request_values.get("preview_mode") or "example").strip().casefold()
        try:
            template_override = None
            if request.method == "POST":
                try:
                    document = _builder_document_from_request()
                except ValueError as exc:
                    raise AgreementPreviewError(str(exc), reason="invalid_template") from exc
                builder_errors = validate_agreement_builder_document(document, fields)
                if builder_errors:
                    errors = [error.as_dict() for error in builder_errors]
                    raise AgreementPreviewError(builder_errors[0].message, reason="invalid_template", errors=errors)
                template_override = render_agreement_builder_template(document)
            template_html, context, _ = _agreement_preview_material(
                form,
                fields,
                mode,
                request.form.get("submission_id") if request.method == "POST" else request.args.get("submission_id"),
                request.form.get("training_id") if request.method == "POST" else request.args.get("training_id"),
                template_override=template_override,
            )
        except AgreementPreviewError as exc:
            return _agreement_preview_error_response(form, mode, exc)
        try:
            rendered_html = render_document_html(current_app._get_current_object(), template_html, context)
            return jsonify({"ok": True, "html": rendered_html})
        except UndefinedError as exc:
            missing = _undefined_variable_name(exc)
            message = _missing_variables_message([missing]) if missing else "Szablon używa zmiennej, dla której nie znaleziono danych."
            error = AgreementPreviewError(message, reason="missing_context_variables", missing_variables=[missing] if missing else [])
            return _agreement_preview_error_response(form, mode, error)
        except Exception:
            current_app.logger.exception("agreement_preview_failed form_id=%s preview_mode=%s", form.id, mode)
            return jsonify({"ok": False, "error": "Nie udało się wygenerować podglądu umowy."}), 500


@bp.route("/forms/<int:form_id>/documents/agreement/example.pdf", methods=["GET", "POST"])
@login_required
def agreement_template_example_pdf(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        fields = active_fields_for_form(db, form.id)
        request_values = request.form if request.method == "POST" else request.args
        mode = str(request_values.get("preview_mode") or "example").strip().casefold()
        try:
            template_override = None
            if request.method == "POST":
                try:
                    document = _builder_document_from_request()
                except ValueError as exc:
                    raise AgreementPreviewError(str(exc), reason="invalid_template") from exc
                builder_errors = validate_agreement_builder_document(document, fields)
                if builder_errors:
                    errors = [error.as_dict() for error in builder_errors]
                    raise AgreementPreviewError(builder_errors[0].message, reason="invalid_template", errors=errors)
                template_override = render_agreement_builder_template(document)
            template_html, context, training = _agreement_preview_material(
                form,
                fields,
                mode,
                request.form.get("submission_id") if request.method == "POST" else request.args.get("submission_id"),
                request.form.get("training_id") if request.method == "POST" else request.args.get("training_id"),
                template_override=template_override,
            )
        except AgreementPreviewError as exc:
            return _agreement_preview_error_response(form, mode, exc)
        try:
            pdf = current_app.extensions["services"].document_service.pdf_render_service.render_document_pdf_bytes(
                app=current_app._get_current_object(),
                template_name="declaration_template.html",
                template_html=template_html,
                context=context,
            )
        except UndefinedError as exc:
            missing = _undefined_variable_name(exc)
            message = _missing_variables_message([missing]) if missing else "Szablon używa zmiennej, dla której nie znaleziono danych."
            error = AgreementPreviewError(message, reason="missing_context_variables", missing_variables=[missing] if missing else [])
            return _agreement_preview_error_response(form, mode, error)
        except Exception:
            current_app.logger.exception("agreement_preview_failed form_id=%s preview_mode=%s", form.id, mode)
            return jsonify({"ok": False, "error": "Nie udało się wygenerować PDF."}), 500
        training_suffix = ""
        if mode == "submission" and training:
            training_suffix = "_" + normalize_slug(str(training.get("id") or training.get("training_id") or training.get("name") or "szkolenie"))
        return send_file(BytesIO(pdf), mimetype="application/pdf", as_attachment=True, download_name=f"przykladowa_umowa_{form.slug}{training_suffix}.pdf")


def _declaration_preview_material(form: Form, fields: list[FormField]) -> tuple[str, dict]:
    definition = form.definition_json or {}
    request_values = request.form if request.method == "POST" else request.args
    mode = str(request_values.get("preview_mode") or "example").strip().casefold()
    if mode not in {"example", "submission"}:
        raise AgreementPreviewError("Nieobsługiwany tryb podglądu.", reason="invalid_preview_mode")
    if request.method == "POST":
        try:
            document = _builder_document_from_request("declaration")
        except ValueError as exc:
            raise AgreementPreviewError(str(exc), reason="invalid_template") from exc
        errors = validate_document_builder_document(
            document,
            fields,
            "declaration",
            form_definition=definition,
        )
        if errors:
            raise AgreementPreviewError(errors[0].message, reason="invalid_template", errors=[error.as_dict() for error in errors])
        template_html = render_document_builder_template(document, "declaration")
    else:
        template_html, template_error = _resolve_declaration_preview_template(definition, fields)
        if template_error:
            raise template_error
    submission = None
    if mode == "submission":
        submission_id = str(request_values.get("submission_id") or "").strip()
        if not submission_id:
            raise AgreementPreviewError("Wybierz zgłoszenie do podglądu deklaracji.", reason="missing_submission")
        submission = current_app.extensions["services"].submission_repository.get_by_id(submission_id)
        if not submission or str(submission.get("form_slug") or "") != form.slug:
            raise AgreementPreviewError("Wybrane zgłoszenie nie należy do tego formularza lub jest niedostępne.", reason="missing_submission")
    context = declaration_preview_context(fields, form_definition=definition, submission=submission)
    context["pdf_image_url"] = current_app.extensions["services"].document_service.resolve_pdf_image_url(definition)
    context["pdf_image_alt"] = definition.get("title", "")
    missing_variables = _missing_agreement_template_variables(template_html, context)
    if missing_variables:
        raise AgreementPreviewError(_missing_variables_message(missing_variables), reason="missing_context_variables", missing_variables=missing_variables)
    return template_html, context


def _resolve_declaration_preview_template(definition: dict, fields: list[FormField] | tuple = ()) -> tuple[str, AgreementPreviewError | None]:
    workflow = definition.get("workflow") or {}
    source = str(workflow.get("declaration_template_source") or "").casefold()
    metadata = workflow.get("declaration_docx_template") or {}
    if not source:
        source = "html" if str(workflow.get("declaration_template_html") or "").strip() else ("docx" if metadata else "builder")
    if source == "builder":
        document = workflow.get("declaration_builder_active_document") or workflow.get("declaration_builder_document") or default_document_builder_document("declaration")
        errors = validate_document_builder_document(
            document,
            fields,
            "declaration",
            form_definition=definition,
        )
        if errors:
            return "", AgreementPreviewError(errors[0].message, reason="invalid_template", errors=[error.as_dict() for error in errors])
        return render_document_builder_template(document, "declaration"), None
    if source == "docx":
        try:
            parsed = current_app.extensions["services"].agreement_docx_template_service.parse_stored_template(metadata)
        except ValueError as exc:
            return "", AgreementPreviewError(str(exc), reason="docx_parse_error")
        unknown = _current_docx_unknown_variables(
            {**metadata, "variables": list(parsed.variables)},
            fields,
            "declaration",
            form_definition=definition,
        )
        if unknown:
            return "", AgreementPreviewError(_missing_variables_message(unknown), reason="missing_context_variables", missing_variables=unknown)
        if str(parsed.html or "").strip():
            return parsed.html, None
        return "", AgreementPreviewError("Nie wgrano szablonu deklaracji Word lub nie udało się go odczytać.", reason="missing_template")
    html_template = str(workflow.get("declaration_template_html") or "").strip()
    if html_template:
        return html_template, None
    return "", AgreementPreviewError("Nie przygotowano szablonu deklaracji.", reason="missing_template")


def _agreement_preview_material(
    form: Form,
    fields: list[FormField],
    preview_mode: str,
    submission_id: str | None,
    training_id: str | None = None,
    *,
    template_override: str | None = None,
) -> tuple[str, dict, dict]:
    definition = form.definition_json or {}
    if preview_mode not in {"example", "submission"}:
        raise AgreementPreviewError("Nieobsługiwany tryb podglądu.", reason="invalid_preview_mode")
    template_html, template_error = (template_override, None) if template_override else _resolve_agreement_preview_template(definition, fields)
    if template_error:
        raise template_error
    submission = None
    selected_training: dict = {}
    if preview_mode == "submission":
        if not submission_id:
            raise AgreementPreviewError("Wybierz zgłoszenie do podglądu umowy.", reason="missing_submission")
        submission = current_app.extensions["services"].submission_repository.get_by_id(submission_id)
        if not submission or str(submission.get("form_slug") or "") != form.slug:
            raise AgreementPreviewError("Wybrane zgłoszenie nie należy do tego formularza lub jest niedostępne.", reason="missing_submission")
        trainings = parse_training_snapshots(submission.get("selected_trainings"))
        if not trainings:
            raise AgreementPreviewError("Wybrane zgłoszenie nie posiada szkolenia, dla którego można wygenerować umowę.", reason="missing_training")
        if training_id:
            selected_training = next(
                (item for item in trainings if str(item.get("id") or item.get("training_id") or "") == str(training_id)),
                {},
            )
            if not selected_training:
                raise AgreementPreviewError("Wybrane szkolenie nie należy do tego zgłoszenia.", reason="missing_training")
        elif len(trainings) > 1:
            raise AgreementPreviewError("Wybierz konkretne szkolenie do podglądu umowy.", reason="missing_training")
        else:
            selected_training = trainings[0]
    context = agreement_preview_context(fields, form_definition=definition, submission=submission, training=selected_training or None)
    context["pdf_image_url"] = current_app.extensions["services"].document_service.resolve_pdf_image_url(definition)
    context["pdf_image_alt"] = definition.get("title", "")
    missing_variables = _missing_agreement_template_variables(template_html, context)
    if missing_variables:
        raise AgreementPreviewError(
            _missing_variables_message(missing_variables),
            reason="missing_context_variables",
            missing_variables=missing_variables,
        )
    return template_html, context, selected_training


def _missing_agreement_template_variables(template_html: str, context: dict) -> list[str]:
    try:
        parsed_template = current_app.jinja_env.parse(template_html)
    except TemplateSyntaxError as exc:
        raise AgreementPreviewError(
            "Szablon umowy zawiera niepoprawną składnię Jinja.",
            reason="invalid_template",
        ) from exc
    available = set(context) | set(current_app.jinja_env.globals)
    return sorted(meta.find_undeclared_variables(parsed_template) - available)


def _missing_variables_message(variable_names: list[str]) -> str:
    formatted = ", ".join("{{ " + name + " }}" for name in variable_names if name)
    if not formatted:
        return "Szablon używa zmiennej, dla której nie znaleziono danych."
    return "Szablon używa zmiennej, dla której nie znaleziono danych: " + formatted + "."


def _undefined_variable_name(exception: UndefinedError) -> str | None:
    match = re.search(r"['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s+is undefined", str(exception))
    return match.group(1) if match else None


def _resolve_agreement_preview_template(definition: dict, fields: list[FormField] | tuple = ()) -> tuple[str, AgreementPreviewError | None]:
    workflow = definition.get("workflow") or {}
    source = str(workflow.get("contract_template_source") or "").casefold()
    metadata = workflow.get("contract_docx_template") or {}
    if not source:
        source = "html" if str(workflow.get("contract_template_html") or "").strip() else ("docx" if metadata else "builder")
    if source == "builder":
        document = workflow.get("contract_builder_active_document") or workflow.get("contract_builder_document") or default_agreement_builder_document()
        errors = validate_agreement_builder_document(document, fields)
        if errors:
            return "", AgreementPreviewError(
                errors[0].message,
                reason="invalid_template",
                errors=[error.as_dict() for error in errors],
            )
        return render_agreement_builder_template(document), None
    if source == "docx":
        try:
            parsed = current_app.extensions["services"].agreement_docx_template_service.parse_stored_template(metadata)
        except ValueError as exc:
            error = AgreementPreviewError(str(exc), reason="docx_parse_error")
            error.__cause__ = exc
            return "", error
        parsed_metadata = {**metadata, "variables": list(parsed.variables)}
        unknown = _current_docx_unknown_variables(parsed_metadata, fields)
        if unknown:
            return "", AgreementPreviewError(
                _missing_variables_message(unknown),
                reason="missing_context_variables",
                missing_variables=unknown,
            )
        docx_html = str(parsed.html or "").strip()
        if docx_html:
            return docx_html, None
        return "", AgreementPreviewError("Nie wgrano szablonu umowy Word lub nie udało się go odczytać.", reason="missing_template")
    html_template = str(workflow.get("contract_template_html") or "").strip()
    if html_template:
        return html_template, None
    return "", AgreementPreviewError("Nie wgrano szablonu umowy Word ani szablonu HTML.", reason="missing_template")


def _current_docx_unknown_variables(
    metadata: dict,
    fields: list[FormField] | tuple,
    document_type: str = "agreement",
    *,
    form_definition: dict | None = None,
) -> list[str]:
    variables = {str(name) for name in metadata.get("variables") or [] if str(name).strip()}
    if not variables:
        return sorted({str(name) for name in metadata.get("unknown_variables") or [] if str(name).strip()})
    available = (
        DeclarationVariableCatalog.context_names(fields, form_definition=form_definition)
        if document_type == "declaration"
        else AgreementVariableCatalog.context_names(fields)
    )
    return sorted(variables - available)


def _document_preview_error_response(form: Form, document_type: str, error: AgreementPreviewError):
    current_app.logger.warning(
        "%s_preview_422 form_id=%s reason=%s missing=%s",
        document_type,
        form.id,
        error.reason,
        error.missing_variables,
    )
    return jsonify(error.as_payload()), 422


def _agreement_preview_error_response(form: Form, preview_mode: str, error: AgreementPreviewError):
    current_app.logger.warning(
        "agreement_preview_422 form_id=%s preview_mode=%s reason=%s missing=%s",
        form.id,
        preview_mode,
        error.reason,
        error.missing_variables,
    )
    if error.reason == "docx_parse_error":
        current_app.logger.exception(
            "agreement_preview_failed form_id=%s preview_mode=%s",
            form.id,
            preview_mode,
        )
    return jsonify(error.as_payload()), 422


def _agreement_docx_editor_context(form: Form, fields: list[FormField]) -> dict:
    definition = form.definition_json or {}
    workflow = definition.get("workflow") or {}
    metadata = dict(workflow.get("contract_docx_template") or {})
    current_unknown = _current_docx_unknown_variables(metadata, fields)
    metadata["unknown_variables"] = current_unknown
    metadata["valid"] = bool(metadata.get("html")) and not current_unknown
    submissions = current_app.extensions["services"].submission_repository.list_by_form(form.slug)
    _, preview_error = _resolve_agreement_preview_template(definition, fields)
    return {
        "agreement_template_variables": agreement_variable_catalog(fields),
        "agreement_docx_metadata": metadata,
        "agreement_preview_available": not preview_error,
        "agreement_preview_error": str(preview_error or ""),
        "agreement_preview_submissions": [
            {
                "submission_id": str(row.get("submission_id") or ""),
                "label": " — ".join(filter(None, [str(row.get("submission_id") or ""), " ".join(filter(None, [str(row.get("imiona") or row.get("first_name") or ""), str(row.get("nazwisko") or row.get("last_name") or "")]))])),
                "trainings": [
                    {
                        "id": str(training.get("id") or training.get("training_id") or ""),
                        "label": str(training.get("name") or training.get("label") or training.get("id") or "Szkolenie"),
                    }
                    for training in parse_training_snapshots(row.get("selected_trainings"))
                ],
            }
            for row in submissions[:100]
            if row.get("submission_id")
        ],
        "agreement_builder_document": normalize_agreement_builder_document(
            workflow.get("contract_builder_document") or default_agreement_builder_document()
        ),
        "agreement_builder_updated_at": workflow.get("contract_builder_updated_at") or "",
        "agreement_builder_is_new": not bool(workflow.get("contract_builder_document")),
        "agreement_builder_can_show_html": g.admin_user.role == ROLE_SUPER_ADMIN,
    }


def _declaration_editor_context(form: Form, fields: list[FormField]) -> dict:
    definition = form.definition_json or {}
    workflow = definition.get("workflow") or {}
    metadata = dict(workflow.get("declaration_docx_template") or {})
    current_unknown = _current_docx_unknown_variables(
        metadata,
        fields,
        "declaration",
        form_definition=definition,
    )
    metadata["unknown_variables"] = current_unknown
    metadata["valid"] = bool(metadata.get("html")) and not current_unknown
    submissions = current_app.extensions["services"].submission_repository.list_by_form(form.slug)
    _, preview_error = _resolve_declaration_preview_template(definition, fields)
    return {
        "declaration_template_variables": declaration_variable_catalog(fields, form_definition=definition),
        "declaration_builder_variables": declaration_builder_variable_catalog(fields, form_definition=definition),
        "declaration_docx_metadata": metadata,
        "declaration_preview_available": not preview_error,
        "declaration_preview_error": str(preview_error or ""),
        "declaration_preview_submissions": [
            {
                "submission_id": str(row.get("submission_id") or ""),
                "label": " — ".join(filter(None, [
                    str(row.get("submission_id") or ""),
                    " ".join(filter(None, [str(row.get("imiona") or row.get("first_name") or ""), str(row.get("nazwisko") or row.get("last_name") or "")]))
                ])),
                "trainings": [],
            }
            for row in submissions[:100]
            if row.get("submission_id")
        ],
        "declaration_builder_document": normalize_document_builder_document(
            workflow.get("declaration_builder_document") or default_document_builder_document("declaration"),
            "declaration",
        ),
        "declaration_builder_updated_at": workflow.get("declaration_builder_updated_at") or "",
        "declaration_builder_is_new": not bool(workflow.get("declaration_builder_document")),
        "declaration_builder_can_show_html": g.admin_user.role == ROLE_SUPER_ADMIN,
    }


def _builder_document_from_request(document_type: str = "agreement") -> dict:
    field_name = "contract_builder_json" if document_type == "agreement" else "declaration_builder_json"
    raw = str(request.form.get("builder_json") or request.form.get(field_name) or "").strip()
    if not raw:
        raise ValueError(f"Brak danych kreatora {'umowy' if document_type == 'agreement' else 'deklaracji'}.")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Konfiguracja kreatora nie jest poprawnym JSON-em.") from exc
    if not isinstance(value, dict):
        raise ValueError("Konfiguracja kreatora musi być obiektem JSON.")
    return normalize_document_builder_document(value, document_type)


def _audit_training_catalog_actions(
    form_slug: str,
    actions: list[dict],
) -> None:
    audit_service = current_app.extensions["services"].audit_log_service
    for action in actions:
        try:
            audit_service.log_event(
                f"TRAINING_{str(action.get('action') or '').upper()}",
                f"form:{form_slug}",
                form_slug,
                old_value=action.get("old_value"),
                new_value=action.get("new_value"),
                actor=str(getattr(g.admin_user, "email", "") or "admin"),
                metadata={
                    "training_id": action.get("training_id"),
                    "training_name": action.get("training_name"),
                    "reason": action.get("reason"),
                    "was_used": bool(action.get("was_used")),
                    "actor_id": action.get("actor_id"),
                    "action_created_at": action.get("created_at"),
                },
            )
        except Exception:
            current_app.logger.exception(
                "training_catalog_audit_failed form_slug=%s training_id=%s",
                form_slug,
                action.get("training_id"),
            )


def _used_training_ids_for_form(db, form_slug: str) -> set[str]:
    """Include normalized rows and legacy selection/agreement JSON."""
    used = {
        str(training_id).strip()
        for training_id in db.execute(
            select(SubmissionTraining.training_id)
            .join(
                FormSubmission,
                FormSubmission.id == SubmissionTraining.submission_id,
            )
            .where(FormSubmission.form_slug == form_slug)
            .distinct()
        ).scalars()
        if str(training_id).strip()
    }
    legacy_rows = db.execute(
        select(
            FormSubmission.selected_trainings,
            FormSubmission.training_agreements,
        ).where(FormSubmission.form_slug == form_slug)
    ).all()
    for selected_trainings, raw_agreements in legacy_rows:
        used.update(
            str(training.get("id") or "").strip()
            for training in parse_training_snapshots(selected_trainings)
            if str(training.get("id") or "").strip()
        )
        try:
            agreements = json.loads(str(raw_agreements or "[]"))
        except (TypeError, json.JSONDecodeError):
            agreements = []
        for agreement in agreements if isinstance(agreements, list) else []:
            if not isinstance(agreement, dict):
                continue
            nested_training = (
                agreement.get("training")
                if isinstance(agreement.get("training"), dict)
                else {}
            )
            training_id = str(
                agreement.get("training_id")
                or nested_training.get("id")
                or agreement.get("id")
                or ""
            ).strip()
            if training_id:
                used.add(training_id)
    return used


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
        _editable_form_version(db, form)
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
        _sync_editable_form_version(db, form)
        db.commit()
    flash("Naprawiono ścieżkę podpisu urzędu. Dodano lub uaktywniono wymagany etap.", "success")
    return redirect(url_for("admin.form_edit", form_id=form_id, tab="workflow"))


@bp.post("/forms/<int:form_id>/share-link/regenerate")
@login_required
def form_share_link_regenerate(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(
            db,
            form_id,
            manage=True,
        )

        if not (
            form.is_active
            and form.is_public
            and not form.is_listed
        ):
            return jsonify(
                {
                    "ok": False,
                    "error": (
                        "Link prywatny można wygenerować tylko "
                        "dla formularza w trybie „Tylko przez link”."
                    ),
                }
            ), 400

        token, token_hash = generate_share_token()

        form.share_token_hash = token_hash

        db.commit()

        share_url = url_for(
            "public_forms.form_page",
            slug=form.slug,
            access=token,
            _external=True,
        )

    return jsonify(
        {
            "ok": True,
            "url": share_url,
        }
    )


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


@bp.post("/forms/<int:form_id>/public/toggle")
@login_required
def form_public_toggle(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(db, form_id, manage=True)
        form.is_public = not form.is_public
        db.commit()
        is_public = form.is_public
    flash("Formularz jest widoczny publicznie." if is_public else "Formularz został ukryty publicznie.", "success")
    return redirect(url_for("admin.forms_list"))


@bp.post("/forms/<int:form_id>/training-selection/toggle")
@login_required
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
        availability_definition, workflow_steps = _field_workflow_context(form)
        if request.method == "POST":
            _editable_form_version(db, form)
            action = request.form.get("action", "save")
            if action == "builder_save":
                try:
                    builder_state = json.loads(request.form.get("builder_state", "[]"))
                    apply_builder_state(
                        db,
                        form,
                        builder_state,
                        field_types=set(FIELD_TYPES),
                        availability_definition=availability_definition,
                    )
                except (json.JSONDecodeError, FormBuilderError) as exc:
                    db.rollback()
                    flash(str(exc) or "Nie udało się odczytać danych edytora.", "error")
                    return redirect(url_for("admin.form_fields", form_id=form.id))
                _sync_editable_form_version(db, form)
                db.commit()
                flash("Układ i pola formularza zostały zapisane.", "success")
                return redirect(url_for("admin.form_fields", form_id=form.id))
            if action == "add":
                field_name = normalize_slug(request.form.get("new_name", "")).replace("-", "_")
                if not field_name:
                    flash("Podaj nazwę pola.", "error")
                    return redirect(url_for("admin.form_fields", form_id=form.id))
                try:
                    new_availability = _availability_from_form(
                        request.form, "new_", availability_definition, workflow_steps
                    )
                except ValueError as exc:
                    flash(str(exc), "error")
                    return redirect(url_for("admin.form_fields", form_id=form.id))
                existing = db.execute(
                    select(FormField).where(FormField.form_id == form.id, FormField.name == field_name)
                ).scalar_one_or_none()
                if existing:
                    existing.active = True
                    existing.label = request.form.get("new_label", "").strip() or existing.label or field_name
                    existing.type = request.form.get("new_type", "text") if request.form.get("new_type") in FIELD_TYPES else "text"
                    existing.section = request.form.get("new_section", "").strip()
                    _set_field_availability(existing, new_availability, availability_definition)
                    existing.sort_order = parse_int(request.form.get("new_sort_order"), len(fields) + 1)
                    existing.options = parse_field_options(existing.type, request.form.get("new_options", ""))
                    saved_field = existing
                else:
                    saved_field = FormField(
                            form_id=form.id,
                            name=field_name,
                            label=request.form.get("new_label", "").strip() or field_name,
                            type=request.form.get("new_type", "text") if request.form.get("new_type") in FIELD_TYPES else "text",
                            required=False,
                            section=request.form.get("new_section", "").strip(),
                            stage=FieldAvailabilityService().initial_step(availability_definition),
                            availability_json=new_availability,
                            sort_order=parse_int(request.form.get("new_sort_order"), len(fields) + 1),
                            options=parse_field_options(request.form.get("new_type", "text"), request.form.get("new_options", "")),
                            active=True,
                        )
                    db.add(saved_field)
                _set_field_availability(saved_field, new_availability, availability_definition)
                _update_document_field_labels(
                    form,
                    {field_name: request.form.get("new_document_label", "").strip()},
                )
                _update_file_field_config(form, field_name, request.form, "new_", saved_field.type)
                _sync_editable_form_version(db, form)
                db.commit()
                flash("Pole formularza zostało dodane.", "success")
                return redirect(url_for("admin.form_fields", form_id=form.id))
            if action.startswith("delete:"):
                field_id = parse_optional_int(action.split(":", 1)[1])
                field = db.get(FormField, field_id) if field_id else None
                if not field or field.form_id != form.id:
                    abort(404)
                field.active = False
                _sync_editable_form_version(db, form)
                db.commit()
                flash("Pole zostało ukryte. Dane historyczne pozostają w zgłoszeniach.", "success")
                return redirect(url_for("admin.form_fields", form_id=form.id))

            for field in fields:
                prefix = f"field_{field.id}_"
                field.label = request.form.get(prefix + "label", "").strip() or field.name
                field.type = request.form.get(prefix + "type", "").strip() if request.form.get(prefix + "type") in FIELD_TYPES else field.type
                field.section = request.form.get(prefix + "section", "").strip()
                try:
                    availability = _availability_from_form(
                        request.form, prefix, availability_definition, workflow_steps, fallback_field=field
                    )
                except ValueError as exc:
                    flash(f"{field.label or field.name}: {exc}", "error")
                    return redirect(url_for("admin.form_fields", form_id=form.id))
                _set_field_availability(field, availability, availability_definition)
                field.sort_order = parse_int(request.form.get(prefix + "sort_order"), field.sort_order)
                field.options = parse_field_options(field.type, request.form.get(prefix + "options", ""))
            _update_document_field_labels(
                form,
                {
                    field.name: request.form.get(f"field_{field.id}_document_label", "").strip()
                    for field in fields
                },
            )
            for field in fields:
                _update_file_field_config(form, field.name, request.form, f"field_{field.id}_", field.type)
            _sync_editable_form_version(db, form)
            db.commit()
            flash("Pola formularza zostały zapisane.", "success")
            return redirect(url_for("admin.form_fields", form_id=form.id))
        definition_fields_by_name = {
            str(item.get("name")): item
            for item in (form.definition_json or {}).get("fields", [])
            if isinstance(item, dict) and item.get("name")
        }
        availability_service = FieldAvailabilityService()
        field_availability = {
            field.name: availability_service.normalize_field(
                {
                    "name": field.name,
                    "stage": field.stage,
                    "required": field.required,
                    "availability": field.availability_json
                    or definition_fields_by_name.get(field.name, {}).get("availability")
                    or [],
                },
                availability_definition,
            )["availability"]
            for field in fields
        }
        return render_template(
            "admin/forms/fields.html",
            form=form,
            fields=fields,
            builder_fields=serialize_builder_fields(form, fields),
            layout_widths=LAYOUT_WIDTHS,
            field_types=FIELD_TYPES,
            field_stages=FIELD_STAGES,
            workflow_steps=workflow_steps,
            field_availability=field_availability,
            initial_workflow_step=FieldAvailabilityService().initial_step(availability_definition),
            field_options_text=field_options_text,
            document_field_labels=(form.definition_json or {}).get("document_field_labels") or {},
            field_configs={
                str(item.get("name")): item
                for item in (form.definition_json or {}).get("fields", [])
                if isinstance(item, dict) and item.get("name")
            },
        )


def _update_file_field_config(form: Form, field_name: str, form_data, prefix: str, field_type: str) -> None:
    if field_type != "file":
        return
    definition = deepcopy(form.definition_json or {})
    fields = list(definition.get("fields") or [])
    config = next((item for item in fields if isinstance(item, dict) and item.get("name") == field_name), None)
    if config is None:
        config = {"name": field_name, "type": "file"}
        fields.append(config)
    split_values = lambda key, default="": [
        item.strip().lower().lstrip(".") for item in str(form_data.get(prefix + key, default) or "").replace("\n", ",").split(",") if item.strip()
    ]
    condition_field = str(form_data.get(prefix + "required_if_field", "") or "").strip()
    condition_operator = str(form_data.get(prefix + "required_if_operator", "equals") or "equals").strip()
    condition_value = str(form_data.get(prefix + "required_if_value", "") or "").strip()
    condition = None
    if condition_field:
        parsed_value = [item.strip() for item in condition_value.split(",") if item.strip()] if condition_operator in {"in", "not_in"} else condition_value
        condition = {"field": condition_field, "operator": condition_operator, "value": parsed_value}
    from services.upload_validation import SAFE_ATTACHMENT_EXTENSIONS, SAFE_ATTACHMENT_MIME_TYPES
    extensions = split_values("allowed_extensions", "pdf") or ["pdf"]
    mime_types = split_values("allowed_mime_types")
    max_size_mb = parse_int(form_data.get(prefix + "max_size_mb"), 10)
    max_files = parse_int(form_data.get(prefix + "max_files"), 1)
    if not set(extensions).issubset(SAFE_ATTACHMENT_EXTENSIONS):
        abort(400, description="Niedozwolone rozszerzenie załącznika.")
    if not set(mime_types).issubset(SAFE_ATTACHMENT_MIME_TYPES):
        abort(400, description="Niedozwolony typ MIME załącznika.")
    if not 1 <= max_size_mb <= 25 or not 1 <= max_files <= 10:
        abort(400, description="Nieprawidłowy limit załączników.")
    config.update({
        "type": "file",
        "description": str(form_data.get(prefix + "description", "") or "").strip(),
        "allowed_extensions": extensions,
        "allowed_mime_types": mime_types,
        "max_size_mb": max_size_mb,
        "max_files": max_files,
        "required_if": condition,
        "document_type": str(form_data.get(prefix + "document_type", "submission_attachment") or "submission_attachment").strip(),
        "category": str(form_data.get(prefix + "category", "") or "").strip(),
    })
    definition["fields"] = fields
    form.definition_json = definition


def _update_document_field_labels(form: Form, updates: dict[str, str]) -> None:
    definition = deepcopy(form.definition_json or {})
    labels = dict(definition.get("document_field_labels") or {})
    for field_name, label in updates.items():
        if label:
            labels[field_name] = label
        else:
            labels.pop(field_name, None)
    if labels:
        definition["document_field_labels"] = labels
    else:
        definition.pop("document_field_labels", None)
    fields = [dict(item) if isinstance(item, dict) else item for item in definition.get("fields") or []]
    for field in fields:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or field.get("id") or "").strip()
        if name not in updates:
            continue
        if updates[name]:
            field["document_label"] = updates[name]
        else:
            field.pop("document_label", None)
    if fields:
        definition["fields"] = fields
    form.definition_json = definition


def format_json(value) -> str:
    import json

    return json.dumps(value or {}, ensure_ascii=False, indent=2)
