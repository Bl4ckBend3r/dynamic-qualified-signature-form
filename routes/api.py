from __future__ import annotations

import logging

from flask import Blueprint, current_app

from services.status_catalog import build_status_view

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


@bp.get("/api/submissions/<submission_id>/acceptance-status")
def api_acceptance_status(submission_id: str):
    submission_id = submission_id.strip()
    if not submission_id:
        return {
            "exists": False,
            "can_sign_documents": False,
            "message": "Nie podano ID wniosku.",
        }, 200

    try:
        submission = get_submission_context(submission_id)
    except Exception as exc:
        logger.exception("Błąd sprawdzania akceptacji wniosku: %s", exc)
        return {
            "exists": False,
            "can_sign_documents": False,
            "message": "Nie udało się sprawdzić statusu wniosku.",
        }, 200

    if not submission:
        return {
            "exists": False,
            "can_sign_documents": False,
            "message": "Nie znaleziono wniosku o podanym ID.",
        }, 200

    if submission["officer_decision"] == "NIE":
        return {
            "exists": True,
            "can_sign_documents": False,
            "message": "Wniosek został odrzucony przez urzędnika.",
            "form_title": submission["form_title"],
            **status_payload(submission["process_status"]),
        }, 200

    if not submission["can_sign_documents"]:
        return {
            "exists": True,
            "can_sign_documents": False,
            "message": "Wniosek nie został jeszcze zaakceptowany przez urzędnika.",
            "form_title": submission["form_title"],
            **status_payload(submission["process_status"]),
        }, 200

    return {
        "exists": True,
        "can_sign_documents": True,
        "message": "Wniosek został zaakceptowany. Możesz przejść do podpisywania dokumentów.",
        "form_title": submission["form_title"],
        "form_slug": submission["form_slug"],
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
