from __future__ import annotations

import json
import logging
from hashlib import sha256

from flask import Blueprint, current_app

from services.status_catalog import build_status_view
from services.process_instruction_service import build_process_instruction_view

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


def get_form_instruction_context(form_slug: str) -> dict:
    services = get_services()
    instruction = ""
    instruction_config = None
    updated_at = ""
    form_config = None
    database_url = str(current_app.config.get("DATABASE_URL") or "").strip()
    if database_url:
        try:
            from database import create_session_factory
            from models import Form
            from sqlalchemy import select

            with create_session_factory(database_url)() as db:
                form = db.execute(select(Form).where(Form.slug == form_slug)).scalar_one_or_none()
                if form:
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
    form_context = get_form_instruction_context(submission["form_slug"])
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
            "stages": [],
        },
        "form_instruction": None,
        "has_form_instruction": False,
        "current_step": None,
        "current_step_label": None,
        "next_action": "",
        "next_action_label": "Co dalej?",
        "instruction_steps": [],
        "instruction_version": None,
    }


@bp.get("/api/submissions/<submission_id>/acceptance-status")
def api_acceptance_status(submission_id: str):
    submission_id = submission_id.strip()
    if not submission_id:
        return {
            "exists": False,
            "can_sign_documents": False,
            "message": "Nie podano ID wniosku.",
            **empty_instruction_payload(),
        }, 200

    try:
        submission = get_submission_context(submission_id)
    except Exception as exc:
        logger.exception("Błąd sprawdzania akceptacji wniosku: %s", exc)
        return {
            "exists": False,
            "can_sign_documents": False,
            "message": "Nie udało się sprawdzić statusu wniosku.",
            **empty_instruction_payload(),
        }, 200

    if not submission:
        return {
            "exists": False,
            "can_sign_documents": False,
            "message": "Nie znaleziono wniosku o podanym ID.",
            **empty_instruction_payload(),
        }, 200

    if submission["officer_decision"] == "NIE":
        return {
            "exists": True,
            "can_sign_documents": False,
            "message": "Wniosek został odrzucony przez urzędnika.",
            "form_title": submission["form_title"],
            **instruction_payload(submission),
            **status_payload(submission["process_status"]),
        }, 200

    if not submission["can_sign_documents"]:
        return {
            "exists": True,
            "can_sign_documents": False,
            "message": "Wniosek nie został jeszcze zaakceptowany przez urzędnika.",
            "form_title": submission["form_title"],
            **instruction_payload(submission),
            **status_payload(submission["process_status"]),
        }, 200

    return {
        "exists": True,
        "can_sign_documents": True,
        "message": "Wniosek został zaakceptowany. Możesz przejść do podpisywania dokumentów.",
        "form_title": submission["form_title"],
        "form_slug": submission["form_slug"],
        **instruction_payload(submission),
        **status_payload(submission["process_status"]),
    }, 200


@bp.get("/api/submissions/<submission_id>/workflow-status")
def api_workflow_status(submission_id: str):
    submission_id = submission_id.strip()
    if not submission_id:
        return {"exists": False, "message": "Nie podano ID wniosku."}, 200

    submission = get_submission_context(submission_id)
    if not submission:
        return {"exists": False, "message": "Nie znaleziono wniosku o podanym ID."}, 200

    services = get_services()
    form_config = services.form_config_service.get_form_config(services.storage, submission["form_slug"]) or {}
    row = submission["row"]
    return {
        "exists": True,
        "submission_id": submission_id,
        "form_slug": submission["form_slug"],
        "form_title": submission["form_title"],
        **status_payload(submission["process_status"]),
        "current_step": services.workflow_service.get_current_step(row, form_config),
        "available_actions": services.workflow_service.get_available_actions(row, form_config),
    }, 200
