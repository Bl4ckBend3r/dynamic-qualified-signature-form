from __future__ import annotations

import sys
from typing import Any

from flask import Flask, abort, flash, redirect, render_template, request, url_for

from form_loader import extract_submission_data, validate_submission
from services.document_service import DocumentType, get_document_config
from services.process_service import ProcessStatus


_original_flask_init = Flask.__init__


def _get_app_module() -> Any:
    return sys.modules.get("app") or sys.modules.get("__main__")


def _build_declaration_form_definition(declaration_config: dict) -> dict:
    fields = declaration_config.get("fields") or []

    if not fields:
        return {}

    return {
        "title": declaration_config.get("form_title") or "Uzupełnienie deklaracji uczestnictwa",
        "description": declaration_config.get("form_description") or "Uzupełnij pola wymagane do wygenerowania deklaracji PDF.",
        "submit_label": declaration_config.get("form_submit_label") or "Wygeneruj deklarację PDF",
        "signature": {"mode": "none"},
        "fields": fields,
    }


def _register_declaration_route(app: Flask) -> None:
    if "declaration_form" in app.view_functions:
        return

    @app.route("/declaration/<slug>/<submission_id>", methods=["GET", "POST"], endpoint="declaration_form")
    def declaration_form(slug: str, submission_id: str):
        app_module = _get_app_module()

        if app_module is None:
            abort(500)

        submission = app_module.find_submission_acceptance_by_id(submission_id)

        if not submission or submission["form_slug"] != slug:
            flash("Nie znaleziono wniosku dla deklaracji.", "error")
            return redirect(url_for("documents_to_sign"))

        if not submission["can_sign_documents"]:
            flash("Wniosek nie został zaakceptowany przez urzędnika.", "error")
            return redirect(url_for("documents_to_sign"))

        form_definition = app_module.get_form_definition(slug)
        if not form_definition:
            abort(404)

        declaration_config = get_document_config(form_definition, DocumentType.DECLARATION)
        declaration_form_definition = _build_declaration_form_definition(declaration_config)

        if not declaration_form_definition:
            flash("Ten formularz nie ma pól deklaracji do uzupełnienia.", "info")
            return redirect(url_for("documents_to_sign", submission_id=submission_id))

        row = submission["row"]
        action_url = url_for("declaration_form", slug=slug, submission_id=submission_id)

        if request.method == "GET":
            return render_template(
                "declaration_form.html",
                form_definition=declaration_form_definition,
                values=row,
                errors={},
                action_url=action_url,
            )

        declaration_data = extract_submission_data(declaration_form_definition, request.form)
        errors = validate_submission(declaration_form_definition, declaration_data)

        if errors:
            values = {**row, **declaration_data}
            return render_template(
                "declaration_form.html",
                form_definition=declaration_form_definition,
                values=values,
                errors=errors,
                action_url=action_url,
            ), 400

        updates = {
            **declaration_data,
            "declaration_form_completed": "Tak",
            "declaration_generated": "",
            "declaration_filename": "",
            "process_status": ProcessStatus.OFFICER_ACCEPTED.value,
        }
        app_module.storage.update_csv_row_by_submission_id(slug, submission_id, updates)

        refreshed_submission = app_module.find_submission_acceptance_by_id(submission_id)
        if not refreshed_submission:
            flash("Nie udało się odświeżyć danych deklaracji.", "error")
            return redirect(url_for("documents_to_sign", submission_id=submission_id))

        declaration = app_module.ensure_declaration_generated(refreshed_submission)

        flash("Deklaracja została uzupełniona i wygenerowana jako PDF.", "success")

        result = {
            "submission_id": submission_id,
            "form_slug": slug,
            "form_title": refreshed_submission["form_title"],
            "message": "Deklaracja została uzupełniona i jest gotowa do podpisania.",
            "declaration_filename": declaration.get("filename", ""),
            "declaration_url": (
                url_for("download_pdf", slug=slug, filename=declaration.get("filename"))
                if declaration.get("enabled") and declaration.get("filename")
                else None
            ),
            "declaration_upload_url": (
                url_for("upload_signed_declaration", slug=slug, submission_id=submission_id)
                if declaration.get("enabled")
                else None
            ),
        }

        return render_template(
            "documents_to_sign.html",
            submission_id=submission_id,
            acceptance_value="Tak",
            errors={},
            result=result,
        )


def _patched_flask_init(self: Flask, *args, **kwargs):
    _original_flask_init(self, *args, **kwargs)
    _register_declaration_route(self)


if not getattr(Flask, "_declaration_route_patch_applied", False):
    Flask.__init__ = _patched_flask_init
    Flask._declaration_route_patch_applied = True
