from __future__ import annotations

import logging

from flask import Blueprint, current_app

from services.process_service import OfficerDecision

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

    try:
        if submission["officer_decision"] != OfficerDecision.MISSING.value:
            get_services().notification_service.send_decision_email(
                submission_id,
                submission["officer_decision"],
            )
    except Exception as exc:
        logger.exception("Nie udało się wysłać e-maila decyzji: %s", exc)

    if submission["officer_decision"] == OfficerDecision.REJECTED.value:
        return {
            "exists": True,
            "can_sign_documents": False,
            "message": "Wniosek został odrzucony przez urzędnika.",
            "form_title": submission["form_title"],
            "process_status": submission["process_status"],
        }, 200

    if not submission["can_sign_documents"]:
        return {
            "exists": True,
            "can_sign_documents": False,
            "message": "Wniosek nie został jeszcze zaakceptowany przez urzędnika.",
            "form_title": submission["form_title"],
            "process_status": submission["process_status"],
        }, 200

    return {
        "exists": True,
        "can_sign_documents": True,
        "message": "Wniosek został zaakceptowany. Możesz przejść do podpisywania dokumentów.",
        "form_title": submission["form_title"],
        "form_slug": submission["form_slug"],
        "process_status": submission["process_status"],
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
        "process_status": submission["process_status"],
        "current_step": services.workflow_service.get_current_step(row, form_config),
        "available_actions": services.workflow_service.get_available_actions(row, form_config),
    }, 200
