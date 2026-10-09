from __future__ import annotations

import json
import mimetypes
import re
import secrets
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
    url_for,
)
from sqlalchemy import func, select

from database import create_session_factory
from models import (
    EmailLog,
    Form,
    FormField,
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
    generate_text_from_html,
    build_instruction_html,
    import_mail_template_zip,
    parse_mail_content,
    render_platform_mail_html,
    render_platform_mail_text,
)
from services.admin_mail_context_service import (
    build_mail_context as service_build_mail_context,
    mail_template_type_score as service_mail_template_type_score,
    preview_mail_context as service_preview_mail_context,
    render_mail_text as service_render_mail_text,
)
from services.admin_form_service import (
    build_definition_from_docx,
    build_definition_from_html,
    detect_form_fields,
    html_attr,
    humanize_field_name,
    parse_workflow_json,
)
from services.logo_service import (
    can_select_active_logo as logo_can_select_active_logo,
    can_select_logo as logo_can_select_logo,
    list_active_logos as logo_list_active_logos,
    list_selectable_logos as logo_list_selectable_logos,
    safe_asset_filename as logo_safe_asset_filename,
)
from services.mail_dispatch_service import MailDispatchService
from services.permission_service import PermissionService


bp = Blueprint("admin", __name__, url_prefix="/admin")

ROLE_SUPER_ADMIN = "super_admin"
ROLE_ADMIN = "admin"
ROLE_FORM_MANAGER = "form_manager"
ROLES = {ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_FORM_MANAGER}
TECHNICAL_SORT_FIELDS = {"created_at", "process_status", "officer_decision", "email", "nazwisko", "submission_id"}
PLACEHOLDER_PATTERN = re.compile(r"{{\s*([a-zA-Z0-9_]+)\s*}}")


@bp.errorhandler(403)
def admin_forbidden(error):
    return render_template(
        "admin/error.html",
        error_title="Brak uprawnień",
        error_message=str(getattr(error, "description", "") or "Nie masz uprawnień do wykonania tej operacji."),
    ), 403
OFFICER_DECISIONS = [
    ("", "Brak decyzji"),
    ("accepted", "Zaakceptowano"),
    ("rejected", "Odrzucono"),
    ("correction", "Do poprawy"),
]
MAIL_TEMPLATE_TYPES = [
    "confirmation",
    "accepted",
    "rejected",
    "correction_required",
    "declaration_signed",
    "agreement_ready",
    "agreement_signed_by_user",
    "agreement_signed_by_office",
    "agreement_rejected_by_office",
    "stage_rollback",
    "auto_rejected_by_condition",
    "returned_for_correction",
    "custom",
    "correction_accepted",
    "form_draft_resume",
    "repeatable_item_decision",
]


def db_session_factory():
    database_url = current_app.config.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for /admin.")
    return create_session_factory(database_url)


from .auth import get_current_user, login_required, permission_required, role_required  # noqa: E402,F401


def normalize_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip().lower()).strip("_")
    return slug or f"form_{secrets.token_hex(4)}"


def safe_asset_filename(value: str) -> str:
    return logo_safe_asset_filename(value)


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
    return logo_list_selectable_logos(db, user, current_logo_id)


def list_active_logos(db) -> list[Logo]:
    return logo_list_active_logos(db)


def can_select_logo(db, user: User, logo_id: int) -> bool:
    return logo_can_select_logo(db, user, logo_id)


def can_select_active_logo(db, logo_id: int) -> bool:
    return logo_can_select_active_logo(db, logo_id)


def build_footer_html(footer: MailFooter | None) -> str:
    return MailDispatchService().build_footer(
        footer,
        logo_url_builder=lambda logo: url_for(
            "public_forms.logo_asset",
            logo_id=logo.id,
            filename=logo.filename,
            _external=True,
        ),
    )


def format_json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, indent=2)


def accessible_form_ids(db, user: User) -> list[int]:
    return PermissionService().accessible_form_ids(db, user)


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


def ensure_form_access(db, form_id: int, manage: bool = False, permission: str | None = None) -> Form:
    form = db.get(Form, form_id)
    if not form:
        abort(404)
    user = g.admin_user
    required = permission or ("can_edit_form" if manage else "can_view_submissions")
    if not PermissionService().has_permission(db, user, required, form=form):
        abort(403)
    return form


def can_manage_form(db, user: User, form_id: int) -> bool:
    """Return the effective write permission, including the role policy."""
    return PermissionService().has_permission(db, user, "can_edit_form", form=form_id)


def has_permission(db, permission: str, *, form=None, submission=None, user=None) -> bool:
    return PermissionService().has_permission(
        db, user or g.admin_user, permission, form=form, submission=submission
    )


def build_mail_context(form: Form, submission: FormSubmission | None, files: list[SubmissionFile]) -> dict:
    from services.training_availability_service import TrainingAvailabilityService

    return service_build_mail_context(
        form,
        submission,
        files,
        documents_to_sign_url_builder=lambda item: url_for(
            "documents.documents_to_sign",
            submission_id=item.submission_id,
            token=item.access_token,
            _external=True,
        ),
        document_url_builder=lambda item, filename: current_app.extensions["services"].document_service.build_download_url(
            {"form_slug": item.form_slug, "submission_id": item.submission_id, "access_token": item.access_token},
            filename,
        ),
        training_availability_service=TrainingAvailabilityService(
            current_app.extensions["services"].submission_repository
        ),
    )


def render_mail_text(raw_text: str, context: dict) -> str:
    return service_render_mail_text(raw_text, context)


def preview_mail_context(form: Form, submission: FormSubmission | None = None) -> dict:
    from services.training_availability_service import TrainingAvailabilityService

    context = build_mail_context(form, submission, [])
    return service_preview_mail_context(
        form,
        submission,
        {
            **context,
            "podpisz_url": context.get("podpisz_url")
            or url_for(
                "documents.documents_to_sign",
                submission_id=context.get("submission_id", ""),
                token=getattr(submission, "access_token", ""),
                _external=True,
            ),
        },
        training_availability_service=TrainingAvailabilityService(
            current_app.extensions["services"].submission_repository
        ),
    )


def select_mail_template(templates: list[MailTemplate], submission: FormSubmission, trigger_event: str) -> MailTemplate | None:
    return MailDispatchService().select_template(templates, submission, trigger_event)


def mail_template_type_score(template: MailTemplate, submission: FormSubmission) -> int:
    return MailDispatchService().mail_template_type_score(template, submission)


from . import dashboard  # noqa: E402,F401
from . import forms  # noqa: E402,F401
from . import logos  # noqa: E402,F401
from . import mail  # noqa: E402,F401
from . import mail_settings  # noqa: E402,F401
from . import site  # noqa: E402,F401
from . import submissions  # noqa: E402,F401
from . import training_management  # noqa: E402,F401
from . import sla  # noqa: E402,F401
from . import users  # noqa: E402,F401
