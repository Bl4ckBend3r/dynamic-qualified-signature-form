from __future__ import annotations

import logging
import hmac
import secrets
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, render_template, request, send_file, session, url_for
from sqlalchemy import select

from database import create_session_factory
from form_loader import FIELD_STAGE_INITIAL, form_definition_for_stage, normalize_form_definition
from models import ContactPage, Form, FormField, FormRegulation, FormSubmission, Logo, ServiceDocument
from services.process_service import ProcessStatus
from services.contact_page_service import ensure_contact_defaults, normalized_phones
from services.site_document_service import SERVICE_DOCUMENT_TYPES
from services.nextcloud_storage import NextcloudStorageError

logger = logging.getLogger(__name__)

bp = Blueprint("public_forms", __name__)
PUBLIC_CSRF_SESSION_KEY = "public_form_csrf_token"


def public_csrf_token() -> str:
    token = str(session.get(PUBLIC_CSRF_SESSION_KEY) or "")
    if not token:
        token = secrets.token_urlsafe(32)
        session[PUBLIC_CSRF_SESSION_KEY] = token
    return token


@bp.app_context_processor
def public_form_csrf_context() -> dict:
    return {"public_csrf_token": public_csrf_token}


def _public_csrf_enabled() -> bool:
    # Existing test/local configurations use this standard switch. Public CSRF
    # remains enabled by default when no explicit switch is present.
    if current_app.config.get("WTF_CSRF_ENABLED") is False:
        return False
    return bool(current_app.config.get("PUBLIC_CSRF_ENABLED", True))


def _provided_public_csrf_token(request_data) -> str:
    value = request.headers.get("X-CSRF-Token", "")
    if not value and hasattr(request_data, "get"):
        value = request_data.get("csrf_token", "")
    return str(value or "")


def _valid_public_csrf(request_data) -> tuple[bool, bool]:
    provided = _provided_public_csrf_token(request_data)
    missing = not bool(provided)
    if not _public_csrf_enabled():
        return True, missing
    expected = str(session.get(PUBLIC_CSRF_SESSION_KEY) or "")
    return bool(expected and provided and hmac.compare_digest(expected, provided)), missing


def get_services():
    return current_app.extensions["services"]


def db_session_factory():
    database_url = current_app.config.get("DATABASE_URL")
    if not database_url:
        return None
    return create_session_factory(database_url)


@bp.get("/")
def index():
    services = get_services()
    if current_app.config.get("DATABASE_URL"):
        forms = list_public_db_forms()
        return render_template("index.html", forms=forms)

    try:
        services.storage.ensure_base_structure()
        services.storage.ensure_outputs_for_all_forms()
        forms = services.form_config_service.list_forms(services.storage)
        return render_template("index.html", forms=forms)
    except NextcloudStorageError as exc:
        logger.exception("Błąd Nextcloud: %s", exc)
        return f"Błąd Nextcloud: {exc}", 500


@bp.get("/form/<slug>")
def form_page(slug: str):
    services = get_services()
    if current_app.config.get("DATABASE_URL"):
        form_meta, form_config = get_public_db_form(slug)
        if not form_meta or not form_config:
            abort(404)
        return render_template(
            "form_page.html",
            slug=slug,
            form_meta=form_meta,
            form_definition=form_definition_for_stage(form_config, FIELD_STAGE_INITIAL),
            errors={},
            values={},
        )

    form_meta = services.form_config_service.get_form_meta(services.storage, slug)
    if not form_meta:
        abort(404)

    form_config = services.form_config_service.get_form_config(services.storage, slug)
    if not form_config:
        abort(404)

    return render_template(
        "form_page.html",
        slug=slug,
        form_meta=form_meta,
        form_definition=form_definition_for_stage(form_config, FIELD_STAGE_INITIAL),
        errors={},
        values={},
    )


@bp.post("/submit/<slug>")
def submit(slug: str):
    services = get_services()
    if current_app.config.get("DATABASE_URL"):
        form_meta, form_config = get_public_db_form(slug)
        if not form_meta or not form_config:
            abort(404)
    else:
        form_meta = services.form_config_service.get_form_meta(services.storage, slug)
        if not form_meta:
            abort(404)

        form_config = services.form_config_service.get_form_config(services.storage, slug)
        if not form_config:
            abort(404)

    request_data = request.get_json(silent=True) if request.is_json else request.form
    csrf_valid, csrf_missing = _valid_public_csrf(request_data or {})
    if not csrf_valid:
        logger.warning(
            "public_form_rejected slug=%s reason=csrf_invalid invalid_fields=%s csrf_missing=%s",
            slug,
            "csrf_token",
            csrf_missing,
        )
        return render_template(
            "form_page.html",
            slug=slug,
            form_meta=form_meta,
            form_definition=form_definition_for_stage(form_config, FIELD_STAGE_INITIAL),
            errors={},
            values=request_data or {},
            form_error="Sesja formularza wygasła lub brakuje tokenu bezpieczeństwa. Odśwież stronę i spróbuj ponownie.",
        ), 400

    try:
        initial_form_config = form_definition_for_stage(form_config, FIELD_STAGE_INITIAL)
        submission_result = services.submission_service.submit_form(slug, initial_form_config, request_data or {})
        if not submission_result["ok"]:
            invalid_fields = sorted(str(name) for name in submission_result["errors"])
            logger.warning(
                "public_form_rejected slug=%s reason=validation invalid_fields=%s csrf_missing=%s",
                slug,
                ",".join(invalid_fields) or "-",
                csrf_missing,
            )
            flash("Formularz zawiera błędy. Popraw wskazane pola.", "error")
            return render_template(
                "form_page.html",
                slug=slug,
                form_meta=form_meta,
                form_definition=initial_form_config,
                errors=submission_result["errors"],
                values=submission_result["values"],
                form_error="Sprawdź pola oznaczone poniżej i popraw wskazane błędy.",
            ), 400

        return render_template("result.html", result=submission_result["result"])

    except Exception as exc:
        logger.exception("Błąd przetwarzania formularza: %s", exc)
        flash("Wystąpił błąd podczas przetwarzania formularza.", "error")
        return render_template(
            "form_page.html",
            slug=slug,
            form_meta=form_meta,
            form_definition=form_definition_for_stage(form_config, FIELD_STAGE_INITIAL),
            errors={},
            values=request_data or request.form,
        ), 500


@bp.route("/form/<slug>/correction/<submission_id>", methods=["GET", "POST"])
def correct_submission(slug: str, submission_id: str):
    """Allow a participant to refill only a submission explicitly returned by an administrator."""
    session_factory = db_session_factory()
    if not session_factory:
        abort(404)
    services = get_services()
    token = str(request.values.get("token") or request.args.get("token") or "").strip()
    with session_factory() as db:
        form = db.execute(
            select(Form).where(
                Form.slug == slug,
                Form.is_active.is_(True),
                Form.is_public.is_(True),
            )
        ).scalar_one_or_none()
        submission = db.execute(
            select(FormSubmission).where(FormSubmission.submission_id == submission_id)
        ).scalar_one_or_none()
        if not form or not submission or submission.form_slug != slug:
            abort(404)
        if not services.access_token_service.verify_token(
            {"access_token": submission.access_token}, token
        ):
            abort(403)
        if submission.process_status != ProcessStatus.RETURNED_FOR_CORRECTION.value:
            abort(404)
        fields = db.execute(
            select(FormField)
            .where(FormField.form_id == form.id, FormField.active.is_(True))
            .order_by(FormField.sort_order, FormField.id)
        ).scalars().all()
        form_meta = form_to_public_meta(form)
        form_config = form_to_definition(form, fields)
        stored_values = {
            key: value
            for key, value in dict(submission.data_json or {}).items()
            if not str(key).startswith("_")
        }
        correction_message = submission.correction_message

    initial_form_config = form_definition_for_stage(form_config, FIELD_STAGE_INITIAL)
    form_action = url_for(
        "public_forms.correct_submission",
        slug=slug,
        submission_id=submission_id,
        token=token,
    )
    if request.method == "GET":
        return render_template(
            "form_page.html",
            slug=slug,
            form_meta=form_meta,
            form_definition=initial_form_config,
            errors={},
            values=stored_values,
            form_action=form_action,
            correction_mode=True,
            correction_message=correction_message,
            access_token=token,
        )

    request_data = request.get_json(silent=True) if request.is_json else request.form
    result = services.submission_service.submit_correction_form(
        slug,
        initial_form_config,
        request_data or {},
        submission_id=submission_id,
        access_token=token,
    )
    if not result["ok"]:
        flash("Formularz zawiera błędy. Popraw wskazane pola.", "error")
        return render_template(
            "form_page.html",
            slug=slug,
            form_meta=form_meta,
            form_definition=initial_form_config,
            errors=result["errors"],
            values=result["values"],
            form_action=form_action,
            correction_mode=True,
            correction_message=correction_message,
            access_token=token,
        ), 400
    return render_template("result.html", result=result["result"])


@bp.get("/kontakt")
def contact_page():
    session_factory = db_session_factory()
    page = None
    if session_factory:
        with session_factory() as db:
            page = db.execute(select(ContactPage).order_by(ContactPage.id)).scalar_one_or_none()
            if page:
                ensure_contact_defaults(page)
                page.phones = normalized_phones(page.phones)
    return render_template("contact.html", page=page)


@bp.get("/dokumenty/<document_type>")
def service_document_page(document_type: str):
    if document_type not in SERVICE_DOCUMENT_TYPES:
        abort(404)
    session_factory = db_session_factory()
    if not session_factory:
        abort(404)
    with session_factory() as db:
        document = db.execute(select(ServiceDocument).where(ServiceDocument.document_type == document_type)).scalar_one_or_none()
        if not document:
            return render_template("service_document.html", document=None, title=SERVICE_DOCUMENT_TYPES[document_type]), 404
        return render_template("service_document.html", document=document, title=document.title)


@bp.get("/dokumenty/<document_type>/plik")
def service_document_file(document_type: str):
    if document_type not in SERVICE_DOCUMENT_TYPES:
        abort(404)
    session_factory = db_session_factory()
    if not session_factory:
        abort(404)
    with session_factory() as db:
        document = db.execute(select(ServiceDocument).where(ServiceDocument.document_type == document_type)).scalar_one_or_none()
        if not document or not document.storage_path:
            abort(404)
        path = Path(document.storage_path)
        if not path.exists():
            abort(404)
        return send_file(path, mimetype=document.mime_type or None, download_name=document.original_filename)


@bp.get("/form/<slug>/regulamin")
def form_regulation_file(slug: str):
    session_factory = db_session_factory()
    if not session_factory:
        abort(404)
    with session_factory() as db:
        regulation = (
            db.execute(
                select(FormRegulation)
                .join(Form)
                .where(
                    Form.slug == slug,
                    Form.is_active.is_(True),
                    Form.is_public.is_(True),
                )
            )
            .scalar_one_or_none()
        )
        if not regulation:
            abort(404)
        path = Path(regulation.storage_path)
        if not path.exists():
            abort(404)
        return send_file(path, mimetype=regulation.mime_type or None, download_name=regulation.original_filename)


@bp.get("/assets/logos/<int:logo_id>/<path:filename>")
def logo_asset(logo_id: int, filename: str):
    session_factory = db_session_factory()
    if not session_factory:
        abort(404)
    with session_factory() as db:
        logo = db.get(Logo, logo_id)
        if not logo or not logo.active or Path(logo.filename).name != Path(filename).name:
            abort(404)
        logo_path = Path(logo.storage_path)
        if not logo_path.exists():
            abort(404)
        return send_file(logo_path, mimetype=logo.mime_type or None)


def list_public_db_forms() -> list[dict]:
    session_factory = db_session_factory()
    if not session_factory:
        return []
    with session_factory() as db:
        forms = db.execute(
            select(Form)
            .where(Form.is_active.is_(True), Form.is_public.is_(True))
            .order_by(Form.sort_order, Form.name)
        ).scalars().all()
        return [form_to_public_meta(form) for form in forms]


def get_public_db_form(slug: str) -> tuple[dict | None, dict | None]:
    session_factory = db_session_factory()
    if not session_factory:
        return None, None
    with session_factory() as db:
        form = db.execute(
            select(Form).where(
                Form.slug == slug,
                Form.is_active.is_(True),
                Form.is_public.is_(True),
            )
        ).scalar_one_or_none()
        if not form:
            return None, None
        fields = db.execute(
            select(FormField)
            .where(FormField.form_id == form.id, FormField.active.is_(True))
            .order_by(FormField.sort_order, FormField.id)
        ).scalars().all()
        return form_to_public_meta(form), form_to_definition(form, fields)


def form_to_public_meta(form: Form) -> dict:
    logo_alignment = normalize_logo_alignment(form.logo_alignment)
    return {
        "slug": form.slug,
        "title": form.title or form.name,
        "description": form.description,
        "label_text": form.label_text,
        "label_variant": form.label_variant,
        "label_color": form.label_color,
        "label_background": form.label_background,
        "logo_url": logo_url(form.logo),
        "logo_alignment": logo_alignment,
        "regulation_url": url_for("public_forms.form_regulation_file", slug=form.slug) if form.regulation else "",
    }


def form_to_definition(form: Form, fields: list[FormField]) -> dict:
    definition = dict(form.definition_json or {})
    logo_alignment = normalize_logo_alignment(form.logo_alignment)
    original_fields = {
        field.get("name"): dict(field)
        for field in definition.get("fields", [])
        if isinstance(field, dict) and field.get("name")
    }
    definition["title"] = form.title or form.name
    definition["description"] = form.description
    definition["fields"] = form_fields_to_definition(fields, original_fields)
    definition["logo_url"] = logo_url(form.logo)
    definition["label_text"] = form.label_text
    definition["label_color"] = form.label_color
    definition["label_background"] = form.label_background
    definition["regulation_url"] = url_for("public_forms.form_regulation_file", slug=form.slug) if form.regulation else ""
    definition["logo_alignment"] = logo_alignment
    return normalize_form_definition(definition)


def form_fields_to_definition(fields: list[FormField], original_fields: dict[str, dict]) -> list[dict]:
    rendered_fields = []
    current_section = None
    for field in fields:
        if field.section and field.section != current_section:
            rendered_fields.append({"type": "section", "label": field.section})
            current_section = field.section
        field_config = dict(original_fields.get(field.name, {}))
        field_config.update(
            {
                "name": field.name,
                "label": field.label or field.name,
                "type": field.type or "text",
                "required": bool(field.required),
                "options": normalize_field_options(field.type, field.options),
                "default": field.default_value,
                "stage": field.stage or FIELD_STAGE_INITIAL,
            }
        )
        rendered_fields.append(field_config)
    return rendered_fields


def normalize_field_options(field_type: str, options) -> list:
    if not isinstance(options, list):
        return []
    if field_type == "checkbox":
        normalized = []
        for option in options:
            if isinstance(option, dict):
                value = str(option.get("value") or option.get("label") or "").strip()
                label = str(option.get("label") or value).strip()
            else:
                value = label = str(option or "").strip()
            if value:
                normalized.append({"value": value, "label": label})
        return normalized
    normalized = []
    for option in options:
        if isinstance(option, dict):
            value = str(option.get("value") or option.get("label") or "").strip()
        else:
            value = str(option or "").strip()
        if value:
            normalized.append(value)
    return normalized


def logo_url(logo: Logo | None) -> str:
    if not logo or not logo.active:
        return ""
    return url_for("public_forms.logo_asset", logo_id=logo.id, filename=Path(logo.filename).name)


def normalize_logo_alignment(value: str | None) -> str:
    return value if value in {"left", "center", "right"} else "left"
