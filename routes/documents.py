from __future__ import annotations

from flask import Blueprint

bp = Blueprint("documents", __name__)


def register_legacy_routes(app_module) -> None:
    bp.add_url_rule("/do-podpisania", view_func=app_module.documents_to_sign, methods=["GET", "POST"])
    bp.add_url_rule(
        "/upload-declaration-signed/<slug>/<submission_id>",
        view_func=app_module.upload_signed_declaration,
        methods=["POST"],
    )
    bp.add_url_rule("/upload-signed/<slug>/<submission_id>", view_func=app_module.upload_signed_pdf, methods=["POST"])
    bp.add_url_rule("/declaration/<slug>/<submission_id>", view_func=app_module.declaration_form, methods=["GET", "POST"])
    bp.add_url_rule(
        "/agreements/<slug>/<submission_id>/generate",
        view_func=app_module.generate_training_agreements,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/agreements/<slug>/<submission_id>/<agreement_id>/upload",
        view_func=app_module.upload_signed_training_agreement,
        methods=["POST"],
    )
    bp.add_url_rule("/result/<slug>/<submission_id>", view_func=app_module.show_result, methods=["GET"])
    bp.add_url_rule("/downloads/pdfs/<slug>/<path:filename>", view_func=app_module.download_pdf, methods=["GET"])
    bp.add_url_rule("/downloads/signed/<slug>/<path:filename>", view_func=app_module.download_signed_pdf, methods=["GET"])
