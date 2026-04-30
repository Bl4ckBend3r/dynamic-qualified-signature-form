from __future__ import annotations

import sys
from functools import wraps
from typing import Any, Callable

from flask import Flask, abort, flash, redirect, render_template, request, url_for

from form_loader import (
    build_consents_view,
    build_submission_view,
    extract_submission_data,
    validate_submission,
)
from services.document_service import (
    DocumentType,
    build_agreement_filename,
    build_document_pdf_context,
    generate_document_pdf_bytes,
    get_document_config,
)
from services.process_service import ProcessStatus


_original_flask_init = Flask.__init__
_original_flask_route = Flask.route

CRITICAL_DECLARATION_REQUIREMENTS = {
    "deklaracja_18_lat": "1. Ukończenie 18 roku życia",
    "deklaracja_lubuskie": "2. Praca, zamieszkanie lub przebywanie na terenie województwa lubuskiego",
    "deklaracja_brak_dzialalnosci": "4. Brak prowadzenia działalności gospodarczej lub oświatowej",
    "deklaracja_brak_ksztalcenia": "5. Brak uczestnictwa w dalszym kształceniu w systemie oświaty",
    "deklaracja_umiejetnosci_podstawowe": "8. Posiadanie umiejętności podstawowych na poziomie 1 i 2 PRK",
}


def _get_app_module() -> Any:
    return sys.modules.get("app") or sys.modules.get("__main__")


def _is_yes(value: Any) -> bool:
    return str(value or "").strip().lower() in {"tak", "yes", "true", "1"}


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


def _find_failed_declaration_requirements(declaration_data: dict) -> list[str]:
    return [
        label
        for field_name, label in CRITICAL_DECLARATION_REQUIREMENTS.items()
        if str(declaration_data.get(field_name, "")).strip().lower() == "nie"
    ]


def _build_agreement_block_reason(failed_requirements: list[str]) -> str:
    if not failed_requirements:
        return ""

    return "Warunki nie zostały spełnione. Odpowiedź 'Nie' wskazano dla: " + "; ".join(failed_requirements) + "."


def _build_documents_result(app_module: Any, submission: dict, message: str = "") -> dict:
    row = submission["row"]
    slug = submission["form_slug"]
    submission_id = submission["submission_id"]

    declaration_filename = str(row.get("declaration_filename", "")).strip()
    agreement_filename = str(row.get("agreement_filename", "")).strip()
    agreement_blocked = _is_yes(row.get("agreement_blocked"))
    agreement_block_reason = str(row.get("agreement_block_reason", "")).strip()

    if not message:
        if agreement_blocked:
            message = "Warunki nie zostały spełnione. Umowa nie zostanie wygenerowana."
        elif agreement_filename:
            message = "Deklaracja została podpisana i poprawnie zweryfikowana. Umowa jest gotowa do podpisania."
        elif declaration_filename:
            message = "Deklaracja jest gotowa do podpisania."
        else:
            message = "Dokumenty są gotowe do obsługi."

    return {
        "submission_id": submission_id,
        "form_slug": slug,
        "form_title": submission["form_title"],
        "message": message,
        "agreement_blocked": agreement_blocked,
        "agreement_block_reason": agreement_block_reason,
        "declaration_filename": declaration_filename,
        "declaration_url": (
            url_for("download_pdf", slug=slug, filename=declaration_filename)
            if declaration_filename
            else None
        ),
        "declaration_upload_url": (
            url_for("upload_signed_declaration", slug=slug, submission_id=submission_id)
            if declaration_filename
            else None
        ),
        "agreement_filename": agreement_filename,
        "agreement_url": (
            url_for("download_pdf", slug=slug, filename=agreement_filename)
            if agreement_filename
            else None
        ),
    }


def _ensure_agreement_generated(app_module: Any, submission_id: str) -> dict:
    submission = app_module.find_submission_acceptance_by_id(submission_id)

    if not submission:
        return {"generated": False, "reason": "Nie znaleziono wniosku."}

    row = submission["row"]
    slug = submission["form_slug"]

    if not _is_yes(row.get("declaration_signature_valid")):
        return {"generated": False, "reason": "Deklaracja nie została poprawnie podpisana."}

    if _is_yes(row.get("agreement_blocked")):
        return {
            "generated": False,
            "blocked": True,
            "reason": row.get("agreement_block_reason") or "Warunki nie zostały spełnione.",
        }

    existing_filename = str(row.get("agreement_filename", "")).strip()
    if _is_yes(row.get("agreement_generated")) and existing_filename:
        return {"generated": False, "filename": existing_filename, "already_exists": True}

    form_definition = app_module.get_form_definition(slug)
    if not form_definition:
        return {"generated": False, "reason": "Nie znaleziono definicji formularza."}

    agreement_config = get_document_config(form_definition, DocumentType.AGREEMENT)
    if not agreement_config.get("enabled"):
        return {"generated": False, "reason": "Umowa jest wyłączona w konfiguracji formularza."}

    agreement_filename = build_agreement_filename(row, agreement_config)
    agreement_context = build_document_pdf_context(
        form_definition=form_definition,
        submission_id=submission_id,
        row=row,
        submission_view=build_submission_view(form_definition, row),
        consents_view=build_consents_view(form_definition, row),
        pdf_image_url=app_module.resolve_pdf_image_url(form_definition),
        document_type=DocumentType.AGREEMENT,
    )
    agreement_template_html = app_module.resolve_nextcloud_template_html(
        agreement_config.get("template", "")
    )
    agreement_bytes = generate_document_pdf_bytes(
        app=app_module.app,
        template_name="agreement_template.html",
        template_html=agreement_template_html,
        context=agreement_context,
    )

    app_module.storage.save_pdf(slug, agreement_filename, agreement_bytes)
    app_module.storage.update_csv_row_by_submission_id(
        slug,
        submission_id,
        {
            "agreement_required": "Tak",
            "agreement_blocked": "Nie",
            "agreement_block_reason": "",
            "agreement_generated": "Tak",
            "agreement_filename": agreement_filename,
            "process_status": ProcessStatus.AGREEMENT_WAITING_FOR_SIGNATURE.value,
        },
    )

    return {"generated": True, "filename": agreement_filename}


def _preserve_submission_redirect(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(*args, **kwargs):
        response = func(*args, **kwargs)
        submission_id = kwargs.get("submission_id")

        if not submission_id:
            return response

        app_module = _get_app_module()
        if app_module is not None:
            agreement_result = _ensure_agreement_generated(app_module, submission_id)
            if agreement_result.get("generated"):
                flash("Umowa została wygenerowana i jest gotowa do podpisania.", "success")
            elif agreement_result.get("blocked"):
                flash("Warunki nie zostały spełnione. Umowa nie zostanie wygenerowana.", "error")
            elif agreement_result.get("reason"):
                flash(agreement_result["reason"], "info")

        if not hasattr(response, "headers"):
            return response

        status_code = getattr(response, "status_code", None)
        location = response.headers.get("Location", "")

        if status_code in {301, 302, 303, 307, 308} and location.endswith(url_for("documents_to_sign")):
            response.headers["Location"] = url_for("documents_to_sign", submission_id=submission_id)

        return response

    return wrapper


def _render_documents_for_get(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(*args, **kwargs):
        if request.method == "GET":
            submission_id = request.args.get("submission_id", "").strip()
            app_module = _get_app_module()

            if submission_id and app_module is not None:
                agreement_result = _ensure_agreement_generated(app_module, submission_id)
                submission = app_module.find_submission_acceptance_by_id(submission_id)

                if submission:
                    message = ""
                    if agreement_result.get("generated") or agreement_result.get("already_exists"):
                        message = "Deklaracja została podpisana i poprawnie zweryfikowana. Umowa jest gotowa do podpisania."
                    elif agreement_result.get("blocked"):
                        message = "Warunki nie zostały spełnione. Umowa nie zostanie wygenerowana."

                    refreshed_submission = app_module.find_submission_acceptance_by_id(submission_id) or submission
                    result = _build_documents_result(app_module, refreshed_submission, message=message)
                    return render_template(
                        "documents_to_sign.html",
                        submission_id=submission_id,
                        acceptance_value="Tak",
                        errors={},
                        result=result,
                    )

        return func(*args, **kwargs)

    return wrapper


def _patched_flask_route(self: Flask, rule: str, **options):
    original_decorator = _original_flask_route(self, rule, **options)

    def decorator(func: Callable):
        endpoint = options.get("endpoint") or func.__name__

        if endpoint == "upload_signed_declaration" or func.__name__ == "upload_signed_declaration":
            return original_decorator(_preserve_submission_redirect(func))

        if endpoint == "documents_to_sign" or func.__name__ == "documents_to_sign":
            return original_decorator(_render_documents_for_get(func))

        return original_decorator(func)

    return decorator


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

        failed_requirements = _find_failed_declaration_requirements(declaration_data)
        agreement_block_reason = _build_agreement_block_reason(failed_requirements)
        agreement_blocked = bool(failed_requirements)

        updates = {
            **declaration_data,
            "declaration_form_completed": "Tak",
            "declaration_generated": "",
            "declaration_filename": "",
            "agreement_blocked": "Tak" if agreement_blocked else "Nie",
            "agreement_block_reason": agreement_block_reason,
            "process_status": (
                ProcessStatus.AGREEMENT_BLOCKED.value
                if agreement_blocked
                else ProcessStatus.OFFICER_ACCEPTED.value
            ),
        }
        app_module.storage.update_csv_row_by_submission_id(slug, submission_id, updates)

        refreshed_submission = app_module.find_submission_acceptance_by_id(submission_id)
        if not refreshed_submission:
            flash("Nie udało się odświeżyć danych deklaracji.", "error")
            return redirect(url_for("documents_to_sign", submission_id=submission_id))

        declaration = app_module.ensure_declaration_generated(refreshed_submission)

        if agreement_blocked:
            flash("Warunki nie zostały spełnione. Umowa nie zostanie wygenerowana.", "error")
        else:
            flash("Deklaracja została uzupełniona i wygenerowana jako PDF.", "success")

        result = {
            "submission_id": submission_id,
            "form_slug": slug,
            "form_title": refreshed_submission["form_title"],
            "message": (
                "Warunki nie zostały spełnione. Umowa nie zostanie wygenerowana."
                if agreement_blocked
                else "Deklaracja została uzupełniona i jest gotowa do podpisania."
            ),
            "agreement_blocked": agreement_blocked,
            "agreement_block_reason": agreement_block_reason,
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
            "agreement_filename": "",
            "agreement_url": None,
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
    Flask.route = _patched_flask_route
    Flask._declaration_route_patch_applied = True
