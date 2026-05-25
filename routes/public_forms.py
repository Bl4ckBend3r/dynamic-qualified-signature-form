from __future__ import annotations

from flask import Blueprint

bp = Blueprint("public_forms", __name__)


def register_legacy_routes(app_module) -> None:
    bp.add_url_rule("/", view_func=app_module.index, methods=["GET"])
    bp.add_url_rule("/form/<slug>", view_func=app_module.form_page, methods=["GET"])
    bp.add_url_rule("/submit/<slug>", view_func=app_module.submit, methods=["POST"])
