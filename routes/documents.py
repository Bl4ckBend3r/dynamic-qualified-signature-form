from __future__ import annotations

import logging
import mimetypes
import tempfile
from datetime import date
from io import BytesIO
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from werkzeug.exceptions import HTTPException

import legacy_app
from form_loader import extract_submission_data, validate_submission
from services.document_service import DocumentType, parse_json_list, serialize_json_list
from services.process_service import build_process_state
from signature_verifier import verify_signed_pdf

logger = logging.getLogger(__name__)

bp = Blueprint("documents", __name__)

DEFAULT_PARTICIPANT_AGREEMENT_SIGNED_NOTIFICATION = {
    "event": "AGREEMENT_SIGNED",
    "to": ["form_notifications"],
    "template": "Template/Mail/agreement_signed.html",
    "subject": "Umowa podpisana przez uczestnika",
}


def get_services():
    return current_app.extensions["services"]


def storage():
    return get_services().storage


def get_form_config(slug: str) -> dict | None:
    services = get_services()
    return services.form_config_service.get_form_config(services.storage, slug)


def get_submission_context(submission_id: str) -> dict | None:
    services = get_services()
    return services.submission_service.get_submission_context(
        submission_id,
        form_config_service=services.form_config_service,
        storage=services.storage,
    )


def normalize_nextcloud_asset_path(asset_path: str) -> str:
    normalized = str(asset_path or "").replace("\\", "/").strip().strip("/")
    forms_dir = current_app.config["NEXTCLOUD_FORMS_DIR"].strip("/")
    output_dir = current_app.config["NEXTCLOUD_OUTPUT_DIR"].strip("/")
    if normalized.startswith((f"{forms_dir}/", f"{output_dir}/")):
        return normalized
    return f"{forms_dir}/{normalized}"


def get_document(form_config: dict, document_id: str) -> dict:
    document = get_services().document_service.get_document_by_id(form_config, document_id)
    return document or {"id": document_id, "enabled": False}


def documents_to_sign_url(submission_id: str | None = None) -> str:
    if submission_id:
        return url_for("documents.documents_to_sign", submission_id=submission_id)
    return url_for("documents.documents_to_sign")


def form_config_with_participant_agreement_notification(form_config: dict) -> tuple[dict, str]:
    notifications = [
        notification
        for notification in form_config.get("notifications", [])
        if isinstance(notification, dict)
    ]
    configured_events = {notification.get("event") for notification in notifications}
    if "AGREEMENT_SIGNED" in configured_events:
        return form_config, "AGREEMENT_SIGNED"

    return {
        **form_config,
        "notifications": [*notifications, DEFAULT_PARTICIPANT_AGREEMENT_SIGNED_NOTIFICATION],
    }, "AGREEMENT_SIGNED"


def send_participant_agreement_signed_notification(
    *,
    services,
    slug: str,
    submission_id: str,
    agreement_id: str | None,
    upload_result: dict,
) -> list[dict]:
    refreshed_submission = get_submission_context(submission_id)
    if not refreshed_submission:
        return []

    row = refreshed_submission["row"]
    if row.get("agreement_signature_valid", "").strip().lower() != "tak":
        return []

    form_config = get_form_config(slug) or {}
    agreements = parse_json_list(row.get("training_agreements"))
    signed_agreement = next(
        (item for item in agreements if str(item.get("id") or "") == str(agreement_id or "")),
        agreements[0] if agreements else {},
    )

    form_config, event_type = form_config_with_participant_agreement_notification(form_config)

    return services.notification_service.notify_event_once(
        event_type,
        refreshed_submission,
        form_config,
        sent_field="agreement_success_email_sent",
        idempotency_key="all",
        context_extra={
            "agreement_id": agreement_id,
            "agreement": signed_agreement,
            "training_agreements": agreements,
            "source_filename": upload_result.get("source_filename"),
            "signed_filename": upload_result.get("signed_filename"),
            "signed_by": "participant",
            "verification": upload_result.get("verification") or {},
        },
    )


def build_declaration_form_definition(declaration_config: dict) -> dict:
    return {
        "title": declaration_config.get("form_title") or "Uzupełnienie deklaracji uczestnictwa",
        "description": declaration_config.get("form_description") or "",
        "submit_label": declaration_config.get("form_submit_label") or "Wygeneruj deklarację PDF",
        "fields": declaration_config.get("fields") or [],
    }


def form_config_with_training_adapter(form_config: dict) -> tuple[dict, dict]:
    document_service = get_services().document_service
    training_document = document_service.get_document_by_id(form_config, DocumentType.TRAINING_AGREEMENT)
    if training_document:
        return form_config, training_document
    agreement_document = document_service.get_document_by_id(form_config, DocumentType.AGREEMENT)
    if not agreement_document:
        return form_config, {"id": DocumentType.TRAINING_AGREEMENT, "enabled": False}
    adapter = {
        **agreement_document,
        "id": DocumentType.TRAINING_AGREEMENT,
        "repeat_over": agreement_document.get("repeat_over") or "selected_trainings",
        "repeat_item_alias": agreement_document.get("repeat_item_alias") or "training",
        "filename_pattern": agreement_document.get("filename_pattern") or "{first_name}_{last_name}-{training_id}-umowa.pdf",
        "numbering": agreement_document.get("numbering") or {
            "number_pattern": "{submission_id}/{agreement_sequence}/{generated_date}",
        },
    }
    return {**form_config, "documents": [*form_config.get("documents", []), adapter]}, adapter


@bp.get("/nextcloud-assets/<path:asset_path>")
def nextcloud_asset(asset_path: str):
    resolved_path = normalize_nextcloud_asset_path(asset_path)

    try:
        if hasattr(storage(), "read_bytes"):
            file_bytes = storage().read_bytes(resolved_path)
        else:
            file_bytes = storage().get_file_bytes(resolved_path)
    except Exception:
        abort(404)

    mime_type, _ = mimetypes.guess_type(Path(resolved_path).name)
    return send_file(
        BytesIO(file_bytes),
        mimetype=mime_type or "application/octet-stream",
        as_attachment=False,
        download_name=Path(resolved_path).name,
    )


@bp.post("/upload-declaration-signed/<slug>/<submission_id>")
def upload_signed_declaration(slug: str, submission_id: str):
    submission = get_submission_context(submission_id)
    if not submission or submission["form_slug"] != slug:
        flash("Nie znaleziono wniosku dla podpisanej deklaracji.", "error")
        return redirect(documents_to_sign_url(submission_id))
    if not submission["can_sign_documents"]:
        flash("Wniosek nie został zaakceptowany przez urzędnika.", "error")
        return redirect(documents_to_sign_url(submission_id))

    try:
        result = get_services().document_service.upload_signed_document(
            submission,
            DocumentType.DECLARATION,
            request.files.get("signed_declaration_pdf"),
        )
        if not result["is_signed"]:
            flash("Przesłany plik nie zawiera podpisu PDF.", "error")
        elif not result["is_valid"]:
            flash("Podpis deklaracji nie jest dopuszczalnym podpisem mSzafir ani Profilem Zaufanym.", "error")
        else:
            flash("Deklaracja została podpisana i poprawnie zweryfikowana.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    except Exception as exc:
        logger.exception("Błąd uploadu podpisanej deklaracji: %s", exc)
        flash("Wystąpił błąd podczas wgrywania lub weryfikacji deklaracji.", "error")

    return redirect(documents_to_sign_url(submission_id))


@bp.route("/declaration/<slug>/<submission_id>", methods=["GET", "POST"])
def declaration_form(slug: str, submission_id: str):
    services = get_services()
    submission = get_submission_context(submission_id)
    if not submission or submission["form_slug"] != slug:
        flash("Nie znaleziono wniosku dla deklaracji.", "error")
        return redirect(documents_to_sign_url(submission_id))
    if not submission["can_sign_documents"]:
        flash("Wniosek nie został zaakceptowany przez urzędnika.", "error")
        return redirect(documents_to_sign_url(submission_id))

    form_config = get_form_config(slug)
    if not form_config:
        abort(404)

    declaration_config = get_document(form_config, DocumentType.DECLARATION)
    if not declaration_config.get("enabled"):
        flash("Deklaracja nie jest wymagana dla tego formularza.", "info")
        return redirect(documents_to_sign_url(submission_id))

    declaration_definition = build_declaration_form_definition(declaration_config)
    values = dict(submission["row"])
    errors = {}

    if request.method == "POST":
        declaration_data = extract_submission_data(declaration_definition, request.form)
        values.update(declaration_data)
        errors = validate_submission(declaration_definition, declaration_data)
        training_field = legacy_app.get_training_selection_field(form_config)

        if training_field:
            selected_trainings, training_error = legacy_app.extract_training_selection(training_field, request.form)
            declaration_data["selected_trainings"] = serialize_json_list(selected_trainings)
            values["selected_trainings"] = declaration_data["selected_trainings"]
            if training_error:
                errors[training_field.get("name", "selected_trainings")] = training_error

        if not errors:
            rule_updates = services.rules_service.apply_rules(submission["row"], form_config, declaration_data)
            updates = {**declaration_data, **rule_updates}
            services.submission_repository.update(submission_id, updates)
            refreshed_submission = get_submission_context(submission_id)
            if refreshed_submission:
                try:
                    services.document_service.generate_document(
                        refreshed_submission,
                        form_config,
                        DocumentType.DECLARATION,
                        force=True,
                    )
                    flash("Deklaracja została wygenerowana.", "success")
                except Exception as exc:
                    logger.exception("Nie udało się wygenerować deklaracji: %s", exc)
                    flash("Nie udało się wygenerować deklaracji.", "error")
            return redirect(documents_to_sign_url(submission_id))

        flash("Deklaracja zawiera błędy. Popraw wskazane pola.", "error")

    return render_template(
        "declaration_form.html",
        form_definition=declaration_definition,
        action_url=url_for("documents.declaration_form", slug=slug, submission_id=submission_id),
        errors=errors,
        values=values,
    )


@bp.post("/agreements/<slug>/<submission_id>/generate")
def generate_training_agreements(slug: str, submission_id: str):
    services = get_services()
    submission = get_submission_context(submission_id)
    if not submission or submission["form_slug"] != slug:
        flash("Nie znaleziono wniosku dla umów.", "error")
        return redirect(documents_to_sign_url(submission_id))
    if submission["row"].get("declaration_signature_valid", "").strip().lower() != "tak":
        flash("Najpierw wgraj poprawnie podpisaną deklarację.", "error")
        return redirect(documents_to_sign_url(submission_id))

    form_config = get_form_config(slug)
    if not form_config:
        abort(404)
    form_config, document = form_config_with_training_adapter(form_config)
    generated_date = date.today().isoformat()

    try:
        agreements = services.document_service.generate_documents_for_collection(
            submission,
            form_config,
            document["id"],
            document.get("repeat_over") or "selected_trainings",
            document.get("repeat_item_alias") or "training",
            context_extra={"generated_date": generated_date},
        )
        flash(f"Wygenerowano umowy: {len(agreements)}.", "success")
    except Exception as exc:
        logger.exception("Nie udało się wygenerować umów szkoleniowych: %s", exc)
        flash("Nie udało się wygenerować umów szkoleniowych.", "error")

    return redirect(documents_to_sign_url(submission_id))


@bp.post("/agreements/<slug>/<submission_id>/<agreement_id>/upload")
def upload_signed_training_agreement(slug: str, submission_id: str, agreement_id: str):
    services = get_services()
    submission = get_submission_context(submission_id)
    if not submission or submission["form_slug"] != slug:
        flash("Nie znaleziono wniosku dla podpisanej umowy.", "error")
        return redirect(documents_to_sign_url(submission_id))

    try:
        result = services.document_service.upload_signed_document(
            submission,
            DocumentType.TRAINING_AGREEMENT,
            request.files.get("signed_agreement_pdf"),
            instance_id=agreement_id,
        )
        if not result["is_signed"]:
            flash("Przesłany plik nie zawiera podpisu PDF.", "error")
        elif not result["is_valid"]:
            flash("Podpis umowy nie jest dopuszczalnym podpisem.", "error")
        else:
            flash("Podpisana umowa została poprawnie zweryfikowana.", "success")
            try:
                sent = send_participant_agreement_signed_notification(
                    services=services,
                    slug=slug,
                    submission_id=submission_id,
                    agreement_id=agreement_id,
                    upload_result=result,
                )
                if sent:
                    flash("Wysłano powiadomienie o umowie podpisanej przez uczestnika.", "success")
            except Exception as exc:
                logger.exception("Nie udało się wysłać powiadomienia AGREEMENT_SIGNED: %s", exc)
                flash("Umowa została podpisana, ale nie udało się wysłać powiadomienia e-mail.", "error")
    except ValueError as exc:
        flash(str(exc), "error")
    except Exception as exc:
        logger.exception("Błąd uploadu podpisanej umowy: %s", exc)
        flash("Wystąpił błąd podczas wgrywania lub weryfikacji umowy.", "error")

    return redirect(documents_to_sign_url(submission_id))


@bp.post("/upload-signed/<slug>/<submission_id>")
def upload_signed_pdf(slug: str, submission_id: str):
    if not get_form_config(slug):
        abort(404)

    try:
        uploaded_file = request.files.get("signed_pdf")
        if not uploaded_file or not uploaded_file.filename:
            flash("Nie wybrano pliku PDF.", "error")
            return redirect(url_for("documents.show_result", slug=slug, submission_id=submission_id))
        if not uploaded_file.filename.lower().endswith(".pdf"):
            flash("Dozwolony jest wyłącznie plik PDF.", "error")
            return redirect(url_for("documents.show_result", slug=slug, submission_id=submission_id))

        signed_pdf_filename = get_services().submission_service.build_signed_pdf_filename(slug, submission_id)
        uploaded_bytes = uploaded_file.read()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=current_app.config["TEMP_DIR"]) as tmp_signed:
            tmp_signed_path = Path(tmp_signed.name)
            tmp_signed.write(uploaded_bytes)

        try:
            verification = verify_signed_pdf(tmp_signed_path)
        finally:
            tmp_signed_path.unlink(missing_ok=True)

        if not verification["is_signed"]:
            flash("Przesłany plik nie zawiera podpisu PDF.", "error")
            return redirect(url_for("documents.show_result", slug=slug, submission_id=submission_id))
        if not verification["is_szafir_signature"]:
            flash("Przesłany plik nie jest podpisem Szafir / KIR.", "error")
            return redirect(url_for("documents.show_result", slug=slug, submission_id=submission_id))

        storage().save_pdf(slug, signed_pdf_filename, uploaded_bytes, signed=True)
        get_services().submission_repository.update(submission_id, {"signed_pdf_filename": signed_pdf_filename})
        flash("Wykryto poprawny podpis Szafir / KIR.", "success")
        return redirect(url_for("documents.show_result", slug=slug, submission_id=submission_id))
    except Exception as exc:
        logger.exception("Błąd uploadu podpisanego PDF: %s", exc)
        flash("Wystąpił błąd podczas wgrywania lub weryfikacji podpisu.", "error")
        return redirect(url_for("documents.show_result", slug=slug, submission_id=submission_id))


@bp.get("/result/<slug>/<submission_id>")
def show_result(slug: str, submission_id: str):
    services = get_services()
    form_config = get_form_config(slug)
    if not form_config:
        abort(404)
    submission = services.submission_repository.get_by_id(submission_id)
    if not submission:
        abort(404)

    pdf_filename = submission.get("pdf_filename") or services.submission_service.build_pdf_filename(slug, submission_id)
    signed_pdf_filename = submission.get("signed_pdf_filename") or services.submission_service.build_signed_pdf_filename(slug, submission_id)

    verification = None
    signed_exists = bool(signed_pdf_filename)
    if signed_exists:
        try:
            signed_pdf_bytes = storage().get_pdf_bytes(slug, signed_pdf_filename)
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=current_app.config["TEMP_DIR"]) as tmp_signed:
                tmp_signed_path = Path(tmp_signed.name)
                tmp_signed.write(signed_pdf_bytes)
            try:
                verification = verify_signed_pdf(tmp_signed_path)
            finally:
                tmp_signed_path.unlink(missing_ok=True)
        except Exception as exc:
            signed_exists = False
            logger.warning("Nie udało się odczytać podpisu: %s", exc)

    result = {
        "submission_id": submission_id,
        "form_slug": slug,
        "pdf_filename": pdf_filename,
        "pdf_url": services.document_service.build_download_url(submission, pdf_filename),
        "signature_request_id": "mobywatel-manual",
        "signature_status": (
            "szafir"
            if verification and verification.get("is_szafir_signature")
            else "uploaded" if signed_exists else "manual"
        ),
        "signed_pdf_filename": signed_pdf_filename if signed_exists else "",
        "signed_pdf_url": (
            services.document_service.build_download_url(submission, signed_pdf_filename, signed=True)
            if signed_exists
            else None
        ),
        "upload_url": url_for("documents.upload_signed_pdf", slug=slug, submission_id=submission_id),
        "form_title": form_config["title"],
        "verification": verification,
    }
    return render_template("result.html", result=result)


def build_documents_to_sign_result(submission_id: str, submission: dict) -> dict:
    services = get_services()
    form_config = get_form_config(submission["form_slug"]) or {}
    declaration = services.document_service.generate_document(
        submission,
        form_config,
        DocumentType.DECLARATION,
    )

    refreshed_submission = get_submission_context(submission_id) or submission
    row = refreshed_submission["row"]
    process_state = build_process_state(row)
    current_step = services.workflow_service.get_current_step(row, form_config)
    available_actions = services.workflow_service.get_available_actions(row, form_config)
    action_targets = {action.get("target_step") for action in available_actions}
    training_agreements = parse_json_list(row.get("training_agreements"))
    selected_trainings = parse_json_list(row.get("selected_trainings"))
    today_iso = date.today().isoformat()
    documents_view = services.document_service.build_documents_view(refreshed_submission, form_config, available_actions)

    return {
        "submission_id": submission_id,
        "form_slug": refreshed_submission["form_slug"],
        "form_title": refreshed_submission["form_title"],
        "message": (
            "Deklaracja została wygenerowana i jest gotowa do podpisania."
            if declaration.get("enabled") and declaration["created"]
            else "Deklaracja była już wygenerowana i jest gotowa do podpisania."
            if declaration.get("enabled")
            else "Dla tego formularza deklaracja nie jest wymagana."
        ),
        "process_status": process_state.status.value,
        "workflow": {
            "current_step": current_step,
            "available_actions": available_actions,
            "documents": documents_view["documents"],
        },
        "available_actions": available_actions,
        "declaration_filename": declaration["filename"],
        "declaration_url": (
            services.document_service.build_download_url(refreshed_submission, declaration["filename"])
            if declaration.get("enabled") and declaration.get("filename")
            else None
        ),
        "declaration_upload_url": (
            url_for("documents.upload_signed_declaration", slug=refreshed_submission["form_slug"], submission_id=submission_id)
            if declaration.get("enabled")
            else None
        ),
        "declaration_signature_valid": row.get("declaration_signature_valid", "").strip().lower() == "tak",
        "agreement_blocked": row.get("agreement_blocked", "").strip().lower() == "tak",
        "agreement_block_reason": row.get("agreement_block_reason", ""),
        "can_generate_agreement": (
            "agreement" in action_targets
            or "training_agreements" in action_targets
            or process_state.can_generate_agreement
            or (
                row.get("declaration_signature_valid", "").strip().lower() == "tak"
                and row.get("agreement_generated", "").strip().lower() != "tak"
                and row.get("agreement_blocked", "").strip().lower() != "tak"
            )
        ) and bool(selected_trainings),
        "generate_agreement_url": url_for(
            "documents.generate_training_agreements",
            slug=refreshed_submission["form_slug"],
            submission_id=submission_id,
        ),
        "agreement_generated": row.get("agreement_generated", "").strip().lower() == "tak",
        "agreement_filename": row.get("agreement_filename", ""),
        "agreement_generated_at": row.get("agreement_generated_at", ""),
        "agreement_generated_at_iso": row.get("agreement_generated_at", "") or today_iso,
        "agreement_signature_valid": row.get("agreement_signature_valid", "").strip().lower() == "tak",
        "agreement_signature_error": row.get("agreement_signature_error", ""),
        "training_agreements": [
            {
                **agreement,
                "url": (
                    services.document_service.build_download_url(
                        refreshed_submission,
                        agreement.get("filename", ""),
                    )
                    if agreement.get("filename")
                    else ""
                ),
                "upload_url": url_for(
                    "documents.upload_signed_training_agreement",
                    slug=refreshed_submission["form_slug"],
                    submission_id=submission_id,
                    agreement_id=agreement.get("id", ""),
                ),
            }
            for agreement in training_agreements
        ],
    }


@bp.route("/do-podpisania", methods=["GET", "POST"])
def documents_to_sign():
    if request.method == "GET":
        submission_id = request.args.get("submission_id", "").strip()
        if submission_id:
            submission = get_submission_context(submission_id)
            errors = {}
            result = None
            status_code = 200
            if not submission:
                errors["submission_id"] = "Nie znaleziono wniosku o podanym ID."
                status_code = 404
            elif not submission["can_sign_documents"]:
                errors["submission_id"] = "Wniosek nie został jeszcze zaakceptowany przez urzędnika."
                status_code = 400
            else:
                try:
                    result = build_documents_to_sign_result(submission_id, submission)
                except Exception as exc:
                    logger.exception("Nie udało się przygotować dokumentów do podpisania: %s", exc)
                    errors["submission_id"] = "Nie udało się przygotować dokumentów do podpisania."
                    status_code = 500

            return render_template(
                "documents_to_sign.html",
                submission_id=submission_id,
                acceptance_value="Tak" if result else "",
                errors=errors,
                result=result,
            ), status_code

        return render_template(
            "documents_to_sign.html",
            submission_id="",
            acceptance_value="",
            errors={},
            result=None,
        )

    services = get_services()
    submission_id = request.form.get("submission_id", "").strip()
    acceptance_value = request.form.get("akceptacja", "").strip()
    errors = {}
    submission = None

    if not submission_id:
        errors["submission_id"] = "Podaj ID wniosku."
    else:
        submission = get_submission_context(submission_id)
        if not submission:
            errors["submission_id"] = "Nie znaleziono wniosku o podanym ID."
        elif not submission["can_sign_documents"]:
            errors["submission_id"] = "Wniosek nie został jeszcze zaakceptowany przez urzędnika."
    if acceptance_value != "Tak":
        errors["akceptacja"] = "Akceptacja dokumentów jest wymagana."

    if errors:
        return render_template(
            "documents_to_sign.html",
            submission_id=submission_id,
            acceptance_value=acceptance_value,
            errors=errors,
            result=None,
        ), 400

    try:
        result = build_documents_to_sign_result(submission_id, submission)
    except Exception as exc:
        logger.exception("Nie udało się przygotować dokumentów do podpisania: %s", exc)
        errors["submission_id"] = "Nie udało się przygotować dokumentów do podpisania."
        return render_template(
            "documents_to_sign.html",
            submission_id=submission_id,
            acceptance_value=acceptance_value,
            errors=errors,
            result=None,
        ), 500

    return render_template(
        "documents_to_sign.html",
        submission_id=submission_id,
        acceptance_value=acceptance_value,
        errors={},
        result=result,
    )

@bp.get("/downloads/pdfs/<slug>/<path:filename>")
def download_pdf(slug: str, filename: str):
    services = get_services()
    try:
        submission = services.submission_repository.find_by_pdf(slug, filename)
        if not submission:
            abort(404)
        if not services.document_service.verify_download_token(submission, request.args.get("token")):
            abort(403)
        pdf_bytes = storage().get_pdf_bytes(slug, filename)
        services.audit_log_service.log_event(
            "DOCUMENT_DOWNLOADED",
            submission.get("submission_id", ""),
            slug,
            metadata={"filename": filename, "signed": False},
        )
    except HTTPException:
        raise
    except Exception:
        abort(404)

    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@bp.get("/downloads/signed/<slug>/<path:filename>")
def download_signed_pdf(slug: str, filename: str):
    services = get_services()
    try:
        submission = services.submission_repository.find_by_pdf(slug, filename)
        if not submission:
            abort(404)
        if not services.document_service.verify_download_token(submission, request.args.get("token")):
            abort(403)
        pdf_bytes = storage().get_pdf_bytes(slug, filename)
        services.audit_log_service.log_event(
            "DOCUMENT_DOWNLOADED",
            submission.get("submission_id", ""),
            slug,
            metadata={"filename": filename, "signed": True},
        )
        return send_file(
            BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
        )
    except HTTPException:
        raise
    except Exception:
        abort(404)
