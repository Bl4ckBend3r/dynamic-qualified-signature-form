from __future__ import annotations

from flask import Blueprint

bp = Blueprint("api", __name__)


def register_legacy_routes(app_module) -> None:
    bp.add_url_rule(
        "/api/submissions/<submission_id>/acceptance-status",
        view_func=app_module.api_acceptance_status,
        methods=["GET"],
    )
