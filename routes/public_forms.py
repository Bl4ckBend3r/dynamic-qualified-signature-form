from __future__ import annotations

import logging
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, render_template, request, send_file, url_for
from sqlalchemy import select

from database import create_session_factory
from form_loader import normalize_form_definition
from models import Form, FormField, Logo
from services.nextcloud_storage import NextcloudStorageError

logger = logging.getLogger(__name__)

bp = Blueprint("public_forms", __name__)


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
            form_definition=form_config,
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
        form_definition=form_config,
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

    try:
        request_data = request.get_json(silent=True) if request.is_json else request.form
        submission_result = services.submission_service.submit_form(slug, form_config, request_data or {})
        if not submission_result["ok"]:
            flash("Formularz zawiera błędy. Popraw wskazane pola.", "error")
            return render_template(
                "form_page.html",
                slug=slug,
                form_meta=form_meta,
                form_definition=form_config,
                errors=submission_result["errors"],
                values=submission_result["values"],
            ), 400

        return render_template("result.html", result=submission_result["result"])

    except Exception as exc:
        logger.exception("Błąd przetwarzania formularza: %s", exc)
        flash("Wystąpił błąd podczas przetwarzania formularza.", "error")
        return render_template(
            "form_page.html",
            slug=slug,
            form_meta=form_meta,
            form_definition=form_config,
            errors={},
            values=request_data or request.form,
        ), 500


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
    return {
        "slug": form.slug,
        "title": form.title or form.name,
        "description": form.description,
        "label_text": form.label_text,
        "label_variant": form.label_variant,
        "label_color": form.label_color,
        "label_background": form.label_background,
        "logo_url": logo_url(form.logo),
    }


def form_to_definition(form: Form, fields: list[FormField]) -> dict:
    definition = dict(form.definition_json or {})
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
