from __future__ import annotations

import json
import logging
from hashlib import sha256

from flask import Blueprint, current_app, jsonify, request

from services.status_catalog import build_status_view
from services.process_instruction_service import build_process_instruction_view
from services.public_submission_status_service import build_public_submission_status
from routes.participant_access import resolve_participant_submission_access

logger = logging.getLogger(__name__)

bp = Blueprint("api", __name__)


def get_services():
    return current_app.extensions["services"]


def get_submission_context(submission_id: str) -> dict | None:
    services = get_services()
    return services.submission_service.get_submission_context(
        submission_id,
        form_config_service=services.form_config_service,
        storage=services.storage,
    )


def _authorized_submission_context(submission_id: str) -> dict | None:
    access = resolve_participant_submission_access(
        submission_id,
        submission_loader=get_submission_context,
    )
    return access.submission if access else None


def _opaque_status_response(*, include_instruction: bool = False):
    payload = {
        "exists": False,
        "authorized": False,
        "can_sign_documents": False,
        "message": "Nie można udostępnić statusu zgłoszenia.",
    }
    if include_instruction:
        payload.update(empty_instruction_payload())
    response = jsonify(payload)
    response.status_code = 404
    response.headers["Cache-Control"] = "no-store"
    return response


def status_payload(process_status: str | None) -> dict:
    view = build_status_view(process_status)
    return {
        "process_status": process_status or "",
        "normalized_process_status": view["current_status"],
        "process_status_label": view["label"],
        "is_final": view["is_final"],
        "is_rejected": view["is_rejected"],
        "requires_user_action": view["requires_user_action"],
        "requires_officer_action": view["requires_officer_action"],
        "declaration_stage_completed": view["declaration_stage_completed"],
        "agreement_stage_completed": view["agreement_stage_completed"],
    }


def get_form_instruction_context(form_slug: str, form_version_id: int | str | None = None) -> dict:
    services = get_services()
    instruction = ""
    instruction_config = None
    updated_at = ""
    form_config = None
    database_url = str(current_app.config.get("DATABASE_URL") or "").strip()
    if database_url:
        try:
            from database import create_session_factory
            from models import Form, FormVersion
            from sqlalchemy import select

            with create_session_factory(database_url)() as db:
                form = db.execute(select(Form).where(Form.slug == form_slug)).scalar_one_or_none()
                if form:
                    version = db.get(FormVersion, int(form_version_id)) if str(form_version_id or "").isdigit() else None
                    if version and version.form_id == form.id:
                        form_config = services.form_config_service.normalize_form_config(version.definition_json or {})
                        instruction = str(form_config.get("user_instruction") or "").strip()
                        instruction_config = form_config.get("user_instruction_config") or {}
                        updated_at = version.updated_at.isoformat() if version.updated_at else ""
                    else:
                        instruction = str(form.user_instruction or "").strip()
                        instruction_config = form.user_instruction_config or {}
                        updated_at = form.updated_at.isoformat() if form.updated_at else ""
                        form_config = services.form_config_service.normalize_form_config(form.definition_json or {})
        except Exception:
            logger.warning("Nie udało się odczytać instrukcji formularza %s z bazy.", form_slug, exc_info=True)
    if form_config is None:
        try:
            form_config = services.form_config_service.get_form_config(services.storage, form_slug) or {}
        except Exception:
            logger.warning("Nie udało się odczytać konfiguracji formularza %s.", form_slug, exc_info=True)
            form_config = {}
        instruction = str(form_config.get("user_instruction") or "").strip()
        instruction_config = form_config.get("user_instruction_config") or {}
        updated_at = str(form_config.get("user_instruction_updated_at") or "")
    return {
        "instruction": instruction,
        "instruction_config": instruction_config or {},
        "updated_at": updated_at,
        "form_config": form_config or {},
    }


def instruction_payload(submission: dict) -> dict:
    form_context = get_form_instruction_context(submission["form_slug"], submission.get("form_version_id"))
    instruction = form_context["instruction"]
    process_view = build_process_instruction_view(
        submission.get("process_status"),
        instruction_config=form_context["instruction_config"],
        legacy_description=instruction,
    )
    version_source = "\0".join(
        [
            json.dumps(process_view["instruction"], ensure_ascii=False, sort_keys=True),
            str(submission.get("process_status") or ""),
        ]
    )
    version = (
        sha256(version_source.encode("utf-8")).hexdigest()
        if process_view["instruction"]["has_instruction"]
        else None
    )
    return {
        "instruction_version": version,
        **process_view,
    }


def empty_instruction_payload() -> dict:
    return {
        "instruction": {
            "title": "",
            "description": "",
            "has_instruction": False,
            "current_stage_key": None,
            "current_stage_label": None,
            "current_stage_description": "",
            "next_action": "",
            "next_stage_key": None,
            "next_stage_label": None,
            "stages": [],
        },
        "form_instruction": None,
        "has_form_instruction": False,
        "current_step": None,
        "current_step_label": None,
        "next_action": "",
        "next_action_label": "Co dalej?",
        "next_stage_label": None,
        "instruction_steps": [],
        "instruction_version": None,
    }


def public_status_payload(submission: dict) -> dict:
    row = submission["row"]
    form_context = get_form_instruction_context(
        submission["form_slug"],
        submission.get("form_version_id") or row.get("form_version_id"),
    )
    form_config = form_context["form_config"]
    current_step = get_services().workflow_service.get_current_step(row, form_config)
    public_status = build_public_submission_status(
        row,
        form_config=form_config,
        current_step=current_step,
    )
    payload = {
        **status_payload(public_status["effective_process_status"]),
        **public_status,
        "raw_process_status": row.get("process_status") or "",
    }
    payload["process_status"] = public_status["effective_process_status"]
    return payload


def reconcile_blocking_instruction(instruction: dict, public_status: dict) -> dict:
    if not public_status.get("agreement_blocked"):
        return instruction
    result = dict(instruction)
    result["next_action"] = public_status["next_action"]
    result["form_instruction"] = public_status["status_description"]
    result["has_form_instruction"] = True
    result["current_step"] = None
    result["current_step_label"] = None
    result["instruction_steps"] = []
    nested = dict(result.get("instruction") or {})
    nested["title"] = public_status["status_title"]
    nested["description"] = public_status["status_description"]
    nested["has_instruction"] = True
    nested["current_stage_key"] = None
    nested["current_stage_label"] = None
    nested["current_stage_description"] = ""
    nested["next_action"] = public_status["next_action"]
    nested["stages"] = []
    result["instruction"] = nested
    return result


@bp.get("/api/submissions/<submission_id>/acceptance-status")
def api_acceptance_status(submission_id: str):
    submission_id = submission_id.strip()
    if not submission_id:
        return _opaque_status_response(include_instruction=True)

    try:
        submission = _authorized_submission_context(submission_id)
    except Exception as exc:
        logger.exception("Błąd sprawdzania statusu zgłoszenia.")
        return _opaque_status_response(include_instruction=True)

    if not submission:
        return _opaque_status_response(include_instruction=True)

    public_status = public_status_payload(submission)
    instructions = reconcile_blocking_instruction(instruction_payload(submission), public_status)
    response = jsonify({
        "exists": True,
        "authorized": True,
        "can_sign_documents": submission["can_sign_documents"],
        "can_view_status_details": submission.get("can_view_status_details", False),
        "message": public_status["status_description"],
        "form_title": submission["form_title"],
        "form_slug": submission["form_slug"],
        **public_status,
        **instructions,
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/api/submissions/<submission_id>/workflow-status")
def api_workflow_status(submission_id: str):
    submission_id = submission_id.strip()
    if not submission_id:
        return _opaque_status_response()

    try:
        submission = _authorized_submission_context(submission_id)
    except Exception:
        logger.exception("Błąd sprawdzania workflow zgłoszenia.")
        return _opaque_status_response()
    if not submission:
        return _opaque_status_response()

    services = get_services()
    form_config = services.form_config_service.get_form_config(services.storage, submission["form_slug"]) or {}
    row = submission["row"]
    public_status = public_status_payload(submission)
    qualification = row.get("data_json", {}).get("_qualification") if isinstance(row.get("data_json"), dict) else None
    response = jsonify({
        "exists": True,
        "authorized": True,
        "submission_id": submission_id,
        "form_slug": submission["form_slug"],
        "form_title": submission["form_title"],
        **public_status,
        "current_step": services.workflow_service.get_current_step(row, form_config),
        "available_actions": (
            []
            if public_status["agreement_blocked"]
            else services.workflow_service.get_available_actions(row, form_config)
        ),
        "qualification_evaluation": qualification,
    })
    response.headers["Cache-Control"] = "no-store"
    return response
