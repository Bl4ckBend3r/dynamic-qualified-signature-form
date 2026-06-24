from __future__ import annotations

# Thin HTTP adapter for document routes.
# Business logic lives in services/documents/*.
# Keep as a single module until route-package split is proven safe.

import logging
import tempfile
from io import BytesIO
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from werkzeug.exceptions import HTTPException
from sqlalchemy import select
from database import create_session_factory
from models import Form
from services.document_service import DocumentType
from services.process_service import build_process_state
from services.workflow_service import workflow_status_label
from signature_verifier import verify_signed_pdf

logger = logging.getLogger(__name__)

bp = Blueprint("documents", __name__)

def get_services():
    return current_app.extensions["services"]


def storage():
    return get_services().storage


def get_form_config(slug: str) -> dict | None:
    services = get_services()
    if current_app.config.get("DATABASE_URL"):
        session_factory = create_session_factory(current_app.config["DATABASE_URL"])
        with session_factory() as db:
            form = db.execute(select(Form).where(Form.slug == slug, Form.is_active.is_(True))).scalar_one_or_none()
            if not form:
                return None
            return services.form_config_service.normalize_form_config(form.definition_json or {})
    return services.form_config_service.get_form_config(services.storage, slug)


def get_submission_context(submission_id: str) -> dict | None:
    services = get_services()
    return services.submission_service.get_submission_context(
        submission_id,
        form_config_service=services.form_config_service,
        storage=services.storage,
    )


def get_document(form_config: dict, document_id: str) -> dict:
    document = get_services().document_service.get_document_by_id(form_config, document_id)
    return document or {"id": document_id, "enabled": False}


def documents_to_sign_url(submission_id: str | None = None) -> str:
    if submission_id:
        return url_for("documents.documents_to_sign", submission_id=submission_id)
    return url_for("documents.documents_to_sign")


def send_participant_agreement_signed_notification(
    *,
    services,
    slug: str,
    submission_id: str,
    agreement_id: str | None,
    upload_result: dict,
) -> list[dict]:
    return services.agreement_flow_service.send_participant_agreement_signed_notification(
        services=services,
        slug=slug,
        submission_id=submission_id,
        agreement_id=agreement_id,
        upload_result=upload_result,
        get_submission_context=get_submission_context,
        get_form_config=get_form_config,
    )


def build_declaration_form_definition(declaration_config: dict) -> dict:
    return get_services().declaration_flow_service.build_declaration_form_definition(declaration_config)


def build_additional_fields_definition(form_config: dict) -> dict:
    return get_services().declaration_flow_service.build_additional_fields_definition(form_config)


def additional_fields_completed(row: dict) -> bool:
    return get_services().declaration_flow_service.additional_fields_completed(row)


def requires_additional_fields(form_config: dict, row: dict) -> bool:
    return get_services().declaration_flow_service.requires_additional_fields(form_config, row)


def form_config_with_training_adapter(form_config: dict) -> tuple[dict, dict]:
    services = get_services()
    return services.agreement_flow_service.form_config_with_training_adapter(
        form_config=form_config,
        document_service=services.document_service,
    )


@bp.get("/nextcloud-assets/<path:asset_path>")
def nextcloud_asset(asset_path: str):
    services = get_services()
    try:
        download = services.document_download_service.prepare_asset(
            storage=services.storage,
            asset_path=asset_path,
            forms_dir=current_app.config["NEXTCLOUD_FORMS_DIR"],
            output_dir=current_app.config["NEXTCLOUD_OUTPUT_DIR"],
        )
    except Exception:
        abort(404)

    return send_file(
        BytesIO(download.pdf_bytes),
        mimetype=download.mimetype,
        as_attachment=False,
        download_name=download.download_name,
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
        result = get_services().document_signing_service.upload_signed_document(
            submission=submission,
            document_id=DocumentType.DECLARATION,
            uploaded_file=request.files.get("signed_declaration_pdf"),
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
    if requires_additional_fields(form_config, submission["row"]):
        flash("Przed pobraniem deklaracji uzupełnij dodatkowe informacje wymagane po akceptacji wniosku.", "error")
        return redirect(documents_to_sign_url(submission_id))

    declaration_config = get_document(form_config, DocumentType.DECLARATION)
    if not declaration_config.get("enabled"):
        flash("Deklaracja nie jest wymagana dla tego formularza.", "info")
        return redirect(documents_to_sign_url(submission_id))

    flow_result = services.declaration_flow_service.prepare_declaration_form(
        submission=submission,
        form_config=form_config,
        declaration_config=declaration_config,
    )

    if request.method == "POST":
        try:
            flow_result = services.declaration_flow_service.handle_declaration_post(
                submission_id=submission_id,
                submission=submission,
                form_config=form_config,
                declaration_config=declaration_config,
                form_data=request.form,
                rules_service=services.rules_service,
                submission_repository=services.submission_repository,
                document_service=services.document_service,
                refresh_submission=get_submission_context,
            )
        except Exception as exc:
            logger.exception("Nie udało się wygenerować deklaracji: %s", exc)
            flash("Nie udało się wygenerować deklaracji.", "error")
            return redirect(documents_to_sign_url(submission_id))

        if flow_result.success:
            flash(flow_result.message or "Deklaracja została wygenerowana.", "success")
            return redirect(documents_to_sign_url(submission_id))

        flash(flow_result.message or "Deklaracja zawiera błędy. Popraw wskazane pola.", "error")

    return render_template(
        "declaration_form.html",
        form_definition=flow_result.declaration_definition,
        action_url=url_for("documents.declaration_form", slug=slug, submission_id=submission_id),
        errors=flow_result.errors,
        values=flow_result.values,
    )


@bp.post("/additional-fields/<slug>/<submission_id>")
def save_additional_fields(slug: str, submission_id: str):
    services = get_services()
    submission = get_submission_context(submission_id)
    if not submission or submission["form_slug"] != slug:
        flash("Nie znaleziono wniosku.", "error")
        return redirect(documents_to_sign_url(submission_id))
    if not submission["can_sign_documents"]:
        flash("Wniosek nie został jeszcze zaakceptowany przez urzędnika.", "error")
        return redirect(documents_to_sign_url(submission_id))
    form_config = get_form_config(slug)
    if not form_config:
        abort(404)
    if not services.declaration_flow_service.has_additional_fields(form_config):
        flash("Ten formularz nie wymaga dodatkowych informacji.", "info")
        return redirect(documents_to_sign_url(submission_id))

    flow_result = services.declaration_flow_service.save_additional_fields(
        submission_id=submission_id,
        submission=submission,
        form_config=form_config,
        form_data=request.form,
        submission_repository=services.submission_repository,
    )
    if not flow_result.success:
        flash(flow_result.message or "Dodatkowe informacje zawierają błędy. Popraw wskazane pola.", "error")
        return render_template(
            "documents_to_sign.html",
            submission_id=submission_id,
            acceptance_value="Tak",
            errors={},
            result=build_documents_to_sign_result(
                submission_id,
                submission,
                additional_errors=flow_result.errors,
                additional_values=flow_result.values,
            ),
        ), 400

    flash(flow_result.message or "Dodatkowe informacje zostały zapisane. Możesz pobrać deklarację.", "success")
    return redirect(documents_to_sign_url(submission_id))


@bp.post("/agreements/<slug>/<submission_id>/generate")
def generate_training_agreements(slug: str, submission_id: str):
    services = get_services()
    submission = get_submission_context(submission_id)
    if not submission or submission["form_slug"] != slug:
        flash("Nie znaleziono wniosku dla umów.", "error")
        return redirect(documents_to_sign_url(submission_id))

    form_config = get_form_config(slug)
    if not form_config:
        abort(404)

    try:
        result = services.agreement_flow_service.generate_training_agreements(
            submission=submission,
            form_config=form_config,
            document_service=services.document_service,
        )
        flash(result.message or "Wygenerowano umowy.", "success" if result.success else "error")
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
        result = services.document_signing_service.upload_signed_document(
            submission=submission,
            document_id=DocumentType.TRAINING_AGREEMENT,
            uploaded_file=request.files.get("signed_agreement_pdf"),
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

    services = get_services()
    try:
        services.document_signing_service.upload_signed_submission_pdf(
            slug=slug,
            submission_id=submission_id,
            uploaded_file=request.files.get("signed_pdf"),
            temp_dir=current_app.config["TEMP_DIR"],
        )
        flash("Wykryto poprawny podpis Szafir / KIR.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
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


def build_documents_to_sign_result(
    submission_id: str,
    submission: dict,
    additional_errors: dict | None = None,
    additional_values: dict | None = None,
) -> dict:
    services = get_services()
    form_config = get_form_config(submission["form_slug"]) or {}
    row = submission["row"]
    if requires_additional_fields(form_config, row):
        return services.document_service.document_view_service.build_additional_fields_result(
            submission_id=submission_id,
            submission=submission,
            form_config=form_config,
            additional_definition=build_additional_fields_definition(form_config),
            additional_action_url=url_for(
                "documents.save_additional_fields",
                slug=submission["form_slug"],
                submission_id=submission_id,
            ),
            status_labeler=workflow_status_label,
            additional_errors=additional_errors,
            additional_values=additional_values,
        )

    declaration = build_existing_declaration_result(services, submission, form_config)

    refreshed_submission = get_submission_context(submission_id) or submission
    row = refreshed_submission["row"]
    process_state = build_process_state(row)
    current_step = services.workflow_service.get_current_step(row, form_config)
    available_actions = services.workflow_service.get_available_actions(row, form_config)
    documents_view = services.document_service.build_documents_view(refreshed_submission, form_config, available_actions)

    return services.document_service.document_view_service.build_documents_to_sign_result(
        submission_id=submission_id,
        submission=refreshed_submission,
        form_config=form_config,
        declaration=declaration,
        process_state=process_state,
        current_step=current_step,
        available_actions=available_actions,
        documents_view=documents_view,
        download_url_builder=lambda filename, signed=False: services.document_service.build_download_url(
            refreshed_submission,
            filename,
            signed=signed,
        ),
        declaration_upload_url=url_for(
            "documents.upload_signed_declaration",
            slug=refreshed_submission["form_slug"],
            submission_id=submission_id,
        ),
        generate_agreement_url=url_for(
            "documents.generate_training_agreements",
            slug=refreshed_submission["form_slug"],
            submission_id=submission_id,
        ),
        agreement_upload_url_builder=lambda agreement_id: url_for(
            "documents.upload_signed_training_agreement",
            slug=refreshed_submission["form_slug"],
            submission_id=submission_id,
            agreement_id=agreement_id,
        ),
        status_labeler=workflow_status_label,
    )


def build_existing_declaration_result(services, submission: dict, form_config: dict) -> dict:
    declaration_config = services.document_service.get_document_by_id(form_config, DocumentType.DECLARATION)
    if not declaration_config or not declaration_config.get("enabled", True):
        return {"enabled": False, "filename": "", "created": False, "document_id": DocumentType.DECLARATION}

    row = submission["row"]
    filename = str(row.get("declaration_filename") or "").strip()
    generated = str(row.get("declaration_generated") or "").strip().lower() == "tak"
    return {
        "enabled": True,
        "filename": filename if generated else "",
        "created": False,
        "document_id": DocumentType.DECLARATION,
        "document": declaration_config,
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
        clean_filename = services.document_download_service.clean_pdf_filename(filename)
        submission = services.submission_repository.find_by_pdf(slug, clean_filename)
        if not submission:
            abort(404)
        form_config = get_form_config(slug) or {}
        if clean_filename == str(submission.get("declaration_filename") or "") and requires_additional_fields(form_config, submission):
            flash("Przed pobraniem deklaracji uzupełnij dodatkowe informacje wymagane po akceptacji wniosku.", "error")
            abort(403)
        if not services.document_download_service.verify_access(
            document_service=services.document_service,
            submission=submission,
            token=request.args.get("token"),
        ):
            abort(403)
        download = services.document_download_service.prepare_download(
            document_service=services.document_service,
            submission=submission,
            filename=clean_filename,
            signed=False,
        )
        services.audit_log_service.log_event(
            "DOCUMENT_DOWNLOADED",
            submission.get("submission_id", ""),
            slug,
            metadata={"filename": clean_filename, "signed": False},
        )
    except HTTPException:
        raise
    except Exception:
        abort(404)

    return send_file(
        BytesIO(download.pdf_bytes),
        mimetype=download.mimetype,
        as_attachment=True,
        download_name=download.download_name,
    )


@bp.get("/downloads/signed/<slug>/<path:filename>")
def download_signed_pdf(slug: str, filename: str):
    services = get_services()
    try:
        clean_filename = services.document_download_service.clean_pdf_filename(filename)
        submission = services.submission_repository.find_by_pdf(slug, clean_filename)
        if not submission:
            abort(404)
        if not services.document_download_service.verify_access(
            document_service=services.document_service,
            submission=submission,
            token=request.args.get("token"),
        ):
            abort(403)
        download = services.document_download_service.prepare_download(
            document_service=services.document_service,
            submission=submission,
            filename=clean_filename,
            signed=True,
        )
        services.audit_log_service.log_event(
            "DOCUMENT_DOWNLOADED",
            submission.get("submission_id", ""),
            slug,
            metadata={"filename": clean_filename, "signed": True},
        )
        return send_file(
            BytesIO(download.pdf_bytes),
            mimetype=download.mimetype,
            as_attachment=True,
            download_name=download.download_name,
        )
    except HTTPException:
        raise
    except Exception:
        abort(404)
