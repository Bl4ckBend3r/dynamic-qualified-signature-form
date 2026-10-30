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

from cryptography import x509
from cryptography.hazmat.primitives import serialization


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


@bp.get("/storage-diagnostics")
@login_required
def storage_diagnostics():
    if g.admin_user.role != ROLE_SUPER_ADMIN:
        abort(403)

    services = current_app.extensions["services"]
    storage = services.storage

    result = {
        "storage_type": type(storage).__name__,
        "failover_enabled": hasattr(storage, "primary"),
        "primary_type": "",
        "primary_available": None,
        "output_dir": "",
        "nextcloud_output_exists": None,
        "nextcloud_error": "",
        "declarations": [],
    }

    primary = storage

    if hasattr(storage, "primary"):
        result["primary_type"] = type(storage.primary).__name__
        result["primary_available"] = storage.primary_available
        primary = storage.primary

    result["output_dir"] = getattr(primary, "output_dir", "")

    try:
        result["nextcloud_output_exists"] = primary.exists(
            primary.output_dir
        )
    except Exception as exc:
        result["nextcloud_error"] = (
            f"{type(exc).__name__}: {exc}"
        )

    with db_session_factory()() as db:
        rows = (
            db.execute(
                select(SubmissionFile)
                .where(
                    SubmissionFile.document_id == "declaration"
                )
                .order_by(SubmissionFile.id.desc())
                .limit(20)
            )
            .scalars()
            .all()
        )

        for row in rows:
            exists = None
            error = ""

            try:
                exists = primary.exists(row.storage_path)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"

            result["declarations"].append(
                {
                    "id": row.id,
                    "submission_id": row.public_submission_id,
                    "form_slug": row.form_slug,
                    "document_type": row.document_type,
                    "filename": row.filename,
                    "storage_path": row.storage_path,
                    "status": row.status,
                    "signed": row.signed,
                    "signature_status": row.signature_status,
                    "workflow_step": row.workflow_step_at_upload,
                    "exists_on_nextcloud": exists,
                    "storage_error": error,
                }
            )

    return result

@bp.get("/signature-diagnostics")
@login_required
def signature_diagnostics():
    if g.admin_user.role != ROLE_SUPER_ADMIN:
        abort(403)

    import hashlib
    import os
    from pathlib import Path

    from cryptography import x509

    from signature_verifier import (
        PROJECT_ROOT,
        check_signature_trust_configuration,
        load_x509_certificate,
    )

    def configured_paths(variable_name):
        result = []

        for value in os.getenv(
            variable_name,
            "",
        ).split(";"):
            value = value.strip()

            if not value:
                continue

            path = Path(value)

            if not path.is_absolute():
                path = PROJECT_ROOT / path

            result.append(path)

        return result

    def display_path(path):
        try:
            return str(
                path.relative_to(PROJECT_ROOT)
            )
        except ValueError:
            return path.name

    def certificate_info(path):
        result = {
            "file": display_path(path),
            "exists": path.exists(),
            "sha256": None,
            "subject": None,
            "issuer": None,
            "valid_from": None,
            "valid_to": None,
            "ca": None,
            "error": "",
        }

        if not path.exists():
            return result

        try:
            cert = load_x509_certificate(path)

            result["sha256"] = hashlib.sha256(
                cert.public_bytes(
                    serialization.Encoding.DER
                )
            ).hexdigest()

            result["subject"] = (
                cert.subject.rfc4514_string()
            )
            result["issuer"] = (
                cert.issuer.rfc4514_string()
            )
            result["valid_from"] = (
                cert.not_valid_before_utc.isoformat()
            )
            result["valid_to"] = (
                cert.not_valid_after_utc.isoformat()
            )

            try:
                constraints = (
                    cert.extensions
                    .get_extension_for_class(
                        x509.BasicConstraints
                    )
                    .value
                )
                result["ca"] = constraints.ca
            except x509.ExtensionNotFound:
                result["ca"] = None

        except Exception as exc:
            result["error"] = (
                f"{type(exc).__name__}: {exc}"
            )

        return result

    def file_info(path):
        result = {
            "file": display_path(path),
            "exists": path.exists(),
            "sha256": None,
            "error": "",
        }

        if not path.exists():
            return result

        try:
            result["sha256"] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        except Exception as exc:
            result["error"] = (
                f"{type(exc).__name__}: {exc}"
            )

        return result

    trust_status = (
        check_signature_trust_configuration()
    )

    roots = [
        certificate_info(path)
        for path in configured_paths(
            "SIGNATURE_TRUST_ROOTS"
        )
    ]

    intermediates = [
        certificate_info(path)
        for path in configured_paths(
            "SIGNATURE_INTERMEDIATE_CERTS"
        )
    ]

    crls = [
        file_info(path)
        for path in configured_paths(
            "SIGNATURE_CRL_FILES"
        )
    ]

    return {
        "trust_configuration": trust_status,
        "allow_fetching": str(
            os.getenv(
                "SIGNATURE_ALLOW_FETCHING",
                "",
            )
        ),
        "trust_roots": roots,
        "intermediate_certificates": intermediates,
        "crl_files": crls,
    }

@bp.get("/runtime-logs")
@login_required
def runtime_logs():
    if g.admin_user.role != ROLE_SUPER_ADMIN:
        abort(403)

    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    handler = current_app.extensions.get(
        "runtime_log_buffer"
    )

    app_timezone = ZoneInfo(
        current_app.config.get(
            "APP_TIMEZONE",
            "Europe/Warsaw",
        )
    )

    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(app_timezone)

    query = str(
        request.args.get("q") or ""
    ).strip()

    level = str(
        request.args.get("level") or ""
    ).strip().upper()

    allowed_levels = {
        "",
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
    }

    if level not in allowed_levels:
        abort(400)

    limit = parse_int(
        request.args.get("limit"),
        200,
    )
    limit = max(10, min(limit, 500))

    raw_from = str(
        request.args.get("date_from") or ""
    ).strip()

    raw_to = str(
        request.args.get("date_to") or ""
    ).strip()

    since_minutes = parse_int(
        request.args.get("since_minutes"),
        0,
    )

    date_from = None
    date_to = None

    def parse_local_datetime(value):
        if not value:
            return None

        parsed = datetime.fromisoformat(value)

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=app_timezone
            )

        return parsed.astimezone(timezone.utc)

    try:
        date_from = parse_local_datetime(
            raw_from
        )
        date_to = parse_local_datetime(
            raw_to
        )
    except ValueError:
        abort(400)

    if since_minutes > 0:
        since_minutes = min(
            since_minutes,
            1440,
        )
        date_from = now_utc - timedelta(
            minutes=since_minutes
        )
        date_to = now_utc

        raw_from = (
            date_from
            .astimezone(app_timezone)
            .strftime("%Y-%m-%dT%H:%M")
        )

        raw_to = (
            now_local
            .strftime("%Y-%m-%dT%H:%M")
        )

    elif not raw_from and not raw_to:
        # Domyślnie ostatnia godzina.
        date_from = now_utc - timedelta(
            hours=1
        )
        date_to = now_utc

        raw_from = (
            date_from
            .astimezone(app_timezone)
            .strftime("%Y-%m-%dT%H:%M")
        )

        raw_to = (
            now_local
            .strftime("%Y-%m-%dT%H:%M")
        )

    if (
        date_from
        and date_to
        and date_from > date_to
    ):
        abort(400)

    logs = []

    if handler is not None:
        logs = handler.snapshot(
            limit=limit,
            level=level,
            query=query,
            date_from=date_from,
            date_to=date_to,
        )

        for entry in logs:
            try:
                timestamp = datetime.fromisoformat(
                    str(
                        entry.get(
                            "timestamp",
                            "",
                        )
                    ).replace(
                        "Z",
                        "+00:00",
                    )
                )

                entry["display_time"] = (
                    timestamp
                    .astimezone(app_timezone)
                    .strftime(
                        "%d.%m.%Y %H:%M:%S"
                    )
                )
            except (TypeError, ValueError):
                entry["display_time"] = (
                    entry.get("timestamp") or "-"
                )

    return render_template(
        "admin/runtime_logs.html",
        logs=logs,
        log_buffer_enabled=handler is not None,
        stored_count=(
            len(handler.records)
            if handler is not None
            else 0
        ),
        filters={
            "q": query,
            "level": level,
            "limit": limit,
            "date_from": raw_from,
            "date_to": raw_to,
            "since_minutes": since_minutes,
        },
    )

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
