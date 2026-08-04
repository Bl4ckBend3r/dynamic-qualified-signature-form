from __future__ import annotations

# Thin HTTP adapter for document routes.
# Business logic lives in services/documents/*.
# Keep as a single module until route-package split is proven safe.

import json
import logging
import secrets
import tempfile
from io import BytesIO
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from werkzeug.exceptions import HTTPException
from sqlalchemy import select
from database import create_session_factory
from models import Form, FormSubmission, SubmissionFile, SubmissionTraining
from services.document_service import DocumentType
from services.submission_training_service import TrainingSelectionError
from services.submission_document_service import SubmissionDocumentType
from services.process_service import build_process_state
from services.training_agreement_service import get_training_selection_field
from services.training_availability_service import TrainingAvailabilityService
from services.training_service import format_price_pln
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


def _training_access_token(submission: dict) -> str:
    return str((submission.get("row") or {}).get("access_token") or "").strip()


def _lock_submission_training(submission_id: str, agreement_id: str) -> bool:
    database_url = current_app.config.get("DATABASE_URL")
    if not database_url:
        return True
    session_factory = create_session_factory(database_url)
    with session_factory() as db:
        submission = db.execute(
            select(FormSubmission).where(FormSubmission.submission_id == submission_id)
        ).scalar_one_or_none()
        agreement_file_id = db.execute(
            select(SubmissionFile.id)
            .where(
                SubmissionFile.public_submission_id == submission_id,
                SubmissionFile.training_key == agreement_id,
                SubmissionFile.signed.is_(True),
            )
            .order_by(SubmissionFile.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if not submission:
            return False
        try:
            locked = get_services().submission_training_service.lock_for_agreement(
                db,
                submission,
                agreement_id,
                agreement_file_id=agreement_file_id,
            )
            db.commit()
            return locked
        except TrainingSelectionError:
            db.rollback()
            if agreement_file_id:
                rejected_file = db.get(SubmissionFile, agreement_file_id)
                if rejected_file:
                    rejected_file.status = "rejected_no_capacity"
                    rejected_file.signed = False
                    db.commit()
            raise


def _mark_training_agreement_downloaded(
    submission_id: str,
    agreement_key: str,
    filename: str,
) -> bool:
    database_url = current_app.config.get("DATABASE_URL")
    if not database_url:
        return False
    session_factory = create_session_factory(database_url)
    with session_factory() as db:
        submission = db.execute(
            select(FormSubmission).where(FormSubmission.submission_id == submission_id)
        ).scalar_one_or_none()
        if not submission:
            return False
        changed = get_services().submission_training_service.mark_agreement_downloaded(
            db,
            submission,
            agreement_key,
            filename=filename,
        )
        if changed:
            db.commit()
        return changed


def _associate_generated_training_agreements(
    submission_id: str, agreements: list[dict]
) -> None:
    database_url = current_app.config.get("DATABASE_URL")
    if not database_url or not agreements:
        return
    session_factory = create_session_factory(database_url)
    with session_factory() as db:
        submission = db.execute(
            select(FormSubmission).where(FormSubmission.submission_id == submission_id)
        ).scalar_one_or_none()
        if submission:
            get_services().submission_training_service.associate_generated_agreements(
                db, submission, agreements
            )
            db.commit()


def _enrich_training_agreement_states(submission: dict) -> None:
    database_url = current_app.config.get("DATABASE_URL")
    if not database_url:
        return
    raw = (submission.get("row") or {}).get("training_agreements")
    if isinstance(raw, str):
        try:
            agreements = json.loads(raw)
        except json.JSONDecodeError:
            return
    else:
        agreements = raw
    if not isinstance(agreements, list):
        return
    session_factory = create_session_factory(database_url)
    with session_factory() as db:
        model = db.execute(
            select(FormSubmission).where(FormSubmission.submission_id == submission.get("submission_id"))
        ).scalar_one_or_none()
        if not model:
            return
        get_services().submission_training_service.synchronize_legacy(db, model)
        rows = db.execute(
            select(SubmissionTraining).where(SubmissionTraining.submission_id == model.id)
        ).scalars().all()
        office_files = db.execute(
            select(SubmissionFile).where(
                SubmissionFile.submission_id == model.id,
                SubmissionFile.document_type == "agreement_signed_by_office",
                SubmissionFile.status == "signed",
            )
        ).scalars().all()
        office_filenames = {item.filename for item in office_files}
        by_key = {
            key: row
            for row in rows
            for key in {str(row.agreement_id or ""), str(row.training_id or "")}
            if key
        }
        for agreement in agreements:
            if not isinstance(agreement, dict):
                continue
            key = str(agreement.get("id") or agreement.get("training_id") or "")
            row = by_key.get(key)
            if not row:
                continue
            agreement.update(
                participant_status=row.status,
                participant_status_label=get_services().submission_training_service.status_label(row.status, row.is_locked),
                is_locked=bool(row.is_locked),
                locked_at=row.locked_at.isoformat() if row.locked_at else "",
                agreement_downloaded=bool(row.agreement_downloaded_at),
                office_signed_filename=(
                    str(agreement.get("signed_filename") or "")
                    if str(agreement.get("signed_filename") or "") in office_filenames
                    else ""
                ),
            )
        db.commit()
        submission["row"]["training_agreements"] = agreements


def _open_training_selection_stage(submission_id: str, slug: str) -> bool:
    database_url = current_app.config.get("DATABASE_URL")
    if not database_url:
        return False
    session_factory = create_session_factory(database_url)
    with session_factory() as db:
        form = db.execute(select(Form).where(Form.slug == slug)).scalar_one_or_none()
        submission = db.execute(
            select(FormSubmission).where(FormSubmission.submission_id == submission_id)
        ).scalar_one_or_none()
        if form is None or submission is None:
            return False
        if not get_services().submission_training_service.open_selection_stage(
            db,
            form,
            submission,
        ):
            return False
        db.commit()
        return True


@bp.route("/submissions/<submission_id>/trainings", methods=["GET", "POST"])
def training_selection(submission_id: str):
    database_url = current_app.config.get("DATABASE_URL")
    if not database_url:
        abort(404)

    token = str(request.values.get("token") or "").strip()
    session_factory = create_session_factory(database_url)
    with session_factory() as db:
        submission = db.execute(
            select(FormSubmission).where(FormSubmission.submission_id == submission_id)
        ).scalar_one_or_none()
        if (
            submission is None
            or not token
            or not secrets.compare_digest(token, str(submission.access_token or ""))
        ):
            abort(404)

        form = db.execute(select(Form).where(Form.slug == submission.form_slug)).scalar_one_or_none()
        if form is None:
            abort(404)
        field = get_training_selection_field(form.definition_json or {})
        if field is None or not field.get("enabled", True):
            abort(404)
        decision = str(
            submission.officer_decision or submission.acceptance_required or ""
        ).strip().lower()
        if (
            decision not in {"tak", "accepted"}
            or str(submission.declaration_generated or "").strip().lower() != "tak"
            or str(submission.declaration_signed or "").strip().lower() != "tak"
            or str(submission.declaration_signature_valid or "").strip().lower() != "tak"
        ):
            abort(403)

        services = get_services()
        availability = TrainingAvailabilityService(
            services.submission_repository
        ).availability_for_field(
            form_slug=form.slug,
            field=field,
            current_submission_id=submission.submission_id,
        )
        error = None
        status_code = 200
        if request.method == "POST":
            if not form.training_selection_open:
                error = "Wybór szkoleń dla tego formularza został zamknięty."
                status_code = 409
            else:
                try:
                    services.submission_training_service.save(
                        db,
                        submission,
                        field,
                        request.form.getlist(str(field.get("name") or "selected_trainings")),
                        availability=availability,
                    )
                    db.commit()
                    flash("Wybór szkoleń został zapisany. Możesz przejść do umów.", "success")
                    return redirect(
                        url_for(
                            "documents.documents_to_sign",
                            submission_id=submission.submission_id,
                        )
                    )
                except TrainingSelectionError as exc:
                    db.rollback()
                    error = str(exc)
                    status_code = 400

        selection_view = services.submission_training_service.selection_view(
            db,
            submission,
            field,
            availability,
            form=form,
        )
        summary = selection_view["summary"]
        catalog = selection_view["catalog"]
        db.commit()
        currency = str(field.get("currency") or "PLN")
        return (
            render_template(
                "training_selection.html",
                submission=submission,
                form=form,
                field=field,
                catalog=catalog,
                selection_open=bool(form.training_selection_open),
                summary=summary,
                limit_total_formatted=format_price_pln(summary["limit_total"], currency)
                if summary["limit_total"] is not None
                else None,
                limit_used_formatted=format_price_pln(summary["limit_used"], currency),
                limit_remaining_formatted=format_price_pln(summary["limit_remaining"], currency)
                if summary["limit_remaining"] is not None
                else None,
                limit_locked_formatted=format_price_pln(summary["limit_locked"], currency),
                limit_pending_formatted=format_price_pln(summary["limit_pending"], currency),
                limit_remaining_after_selection_formatted=format_price_pln(
                    summary["limit_remaining_after_selection"], currency
                ) if summary["limit_remaining_after_selection"] is not None else None,
                error=error,
                action_url=url_for(
                    "documents.training_selection",
                    submission_id=submission.submission_id,
                    token=token,
                ),
            ),
            status_code,
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
            token = _training_access_token(submission)
            if token and _open_training_selection_stage(submission_id, slug):
                return redirect(
                    url_for(
                        "documents.training_selection",
                        submission_id=submission_id,
                        token=token,
                    )
                )
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
        submission_repository=services.submission_repository,
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
        if result.success:
            _associate_generated_training_agreements(submission_id, result.agreements)
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
            if not _lock_submission_training(submission_id, agreement_id):
                raise ValueError("Nie znaleziono szkolenia przypisanego do tej umowy.")
            flash("Podpisana umowa została wgrana. Szkolenie i miejsce zostały zablokowane.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    except Exception as exc:
        logger.exception("Błąd uploadu podpisanej umowy: %s", exc)
        flash("Wystąpił błąd podczas wgrywania lub weryfikacji umowy.", "error")

    return redirect(documents_to_sign_url(submission_id))


@bp.post("/agreements/<slug>/<submission_id>/upload-all")
def upload_signed_training_agreements(slug: str, submission_id: str):
    """Upload multiple signed training agreements while preserving per-file results."""
    services = get_services()
    submission = get_submission_context(submission_id)
    if not submission or submission["form_slug"] != slug:
        return jsonify({"ok": False, "error": "Nie znaleziono wniosku dla podpisanych umów."}), 404

    files = request.files.getlist("signed_agreement_files")
    agreement_ids = request.form.getlist("agreement_ids")
    if not files or len(files) != len(agreement_ids):
        return jsonify({"ok": False, "error": "Każdy plik musi być przypisany do jednej umowy."}), 400

    raw_agreements = (submission.get("row") or {}).get("training_agreements", [])
    if isinstance(raw_agreements, str):
        try:
            raw_agreements = json.loads(raw_agreements)
        except json.JSONDecodeError:
            raw_agreements = []
    known_ids = {
        str(item.get("id") or item.get("agreement_id") or "")
        for item in raw_agreements
        if isinstance(item, dict)
    }
    results = []
    assigned_ids = set()
    for agreement_id, uploaded_file in zip(agreement_ids, files, strict=True):
        filename = Path(uploaded_file.filename or "").name
        item = {"agreement_id": agreement_id, "filename": filename, "status": "error", "message": ""}
        if not agreement_id or agreement_id not in known_ids:
            item["message"] = "Nieprawidłowe przypisanie pliku do umowy."
            results.append(item)
            continue
        if agreement_id in assigned_ids:
            item["message"] = "Ta umowa została już przypisana do innego pliku."
            results.append(item)
            continue
        assigned_ids.add(agreement_id)
        try:
            verification = services.document_signing_service.upload_signed_document(
                submission=submission,
                document_id=DocumentType.TRAINING_AGREEMENT,
                uploaded_file=uploaded_file,
                instance_id=agreement_id,
            )
            if not verification["is_signed"]:
                item["message"] = "Plik nie zawiera podpisu PDF."
            elif not verification["is_valid"]:
                item["message"] = "Podpis umowy nie jest dopuszczalnym podpisem."
            else:
                if not _lock_submission_training(submission_id, agreement_id):
                    raise ValueError("Nie znaleziono szkolenia przypisanego do tej umowy.")
                item.update(status="uploaded", message="Wgrano, zweryfikowano i zablokowano szkolenie.")
        except ValueError as exc:
            item["message"] = str(exc)
        except Exception:
            logger.exception(
                "Batch agreement upload failed public_submission_id=%s agreement_id=%s filename=%s",
                submission_id,
                agreement_id,
                filename,
            )
            item["message"] = "Wystąpił błąd podczas wgrywania lub weryfikacji."
        results.append(item)

    uploaded_count = sum(item["status"] == "uploaded" for item in results)
    return jsonify(
        {
            "ok": uploaded_count == len(results),
            "uploaded": uploaded_count,
            "failed": len(results) - uploaded_count,
            "results": results,
        }
    ), 200


@bp.post("/agreement/<slug>/<submission_id>/upload")
def upload_signed_agreement(slug: str, submission_id: str):
    submission = get_submission_context(submission_id)
    if not submission or submission["form_slug"] != slug:
        flash("Nie znaleziono wniosku dla podpisanej umowy.", "error")
        return redirect(documents_to_sign_url(submission_id))
    try:
        result = get_services().document_signing_service.upload_signed_document(
            submission=submission,
            document_id=DocumentType.AGREEMENT,
            uploaded_file=request.files.get("signed_agreement_pdf"),
        )
        if not result["is_signed"]:
            flash("Przesłany plik nie zawiera podpisu PDF.", "error")
        elif not result["is_valid"]:
            flash("Podpis umowy nie jest dopuszczalnym podpisem.", "error")
        else:
            flash("Podpisana umowa została poprawnie zweryfikowana.", "success")
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
    _enrich_training_agreement_states(refreshed_submission)
    row = refreshed_submission["row"]
    process_state = build_process_state(row)
    current_step = services.workflow_service.get_current_step(row, form_config)
    available_actions = services.workflow_service.get_available_actions(row, form_config)
    documents_view = services.document_service.build_documents_view(refreshed_submission, form_config, available_actions)
    configured_agreement = services.document_service.get_document_by_id(form_config, DocumentType.AGREEMENT)
    configured_training_agreement = services.document_service.get_document_by_id(
        form_config, DocumentType.TRAINING_AGREEMENT
    )
    agreement_document = (
        configured_agreement
        if configured_agreement and str(configured_agreement.get("template_html") or "").strip()
        else configured_training_agreement
        if configured_training_agreement and configured_training_agreement.get("enabled", True)
        else configured_agreement
    )
    agreement_required = bool(agreement_document and agreement_document.get("enabled", True))
    agreement_template_configured = bool(
        agreement_required
        and str(agreement_document.get("template_html") or agreement_document.get("template") or "").strip()
    )

    result = services.document_service.document_view_service.build_documents_to_sign_result(
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
        agreement_upload_url=url_for(
            "documents.upload_signed_agreement",
            slug=refreshed_submission["form_slug"],
            submission_id=submission_id,
        ),
        agreement_required=agreement_required,
        agreement_template_configured=agreement_template_configured,
        status_labeler=workflow_status_label,
        available_filenames=documents_view.get("available_filenames", set()),
    )
    token = _training_access_token(refreshed_submission)
    training_field = get_training_selection_field(form_config)
    result["training_selection_url"] = (
        url_for(
            "documents.training_selection",
            submission_id=submission_id,
            token=token,
        )
        if token
        and result.get("declaration_signature_valid")
        and training_field
        and training_field.get("enabled", True)
        else ""
    )
    return result


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
            elif not submission["can_sign_documents"] and not submission.get("can_view_status_details"):
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
        elif not submission["can_sign_documents"] and not submission.get("can_view_status_details"):
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
        services.submission_document_service.backfill_existing_legacy_documents(submission)
        metadata = services.submission_repository.get_file_metadata(
            submission.get("submission_id", ""), clean_filename, signed=None
        )
        allowed_types = {
            "",
            SubmissionDocumentType.FORM_PDF,
            SubmissionDocumentType.DECLARATION,
            SubmissionDocumentType.AGREEMENT,
            SubmissionDocumentType.TRAINING_AGREEMENT,
            SubmissionDocumentType.SIGNED_AGREEMENT,
            SubmissionDocumentType.SIGNED_TRAINING_AGREEMENT,
            "agreement_signed_by_office",
        }
        if metadata and str(metadata.get("document_type") or "") not in allowed_types:
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
            signed=bool((metadata or {}).get("signed", False)),
        )
        logger.info(
            "Document download public_submission_id=%s internal_submission_id=%s filename=%s "
            "document_type=%s storage_path=%s file_found=%s.",
            submission.get("submission_id", ""),
            submission.get("id", ""),
            clean_filename,
            (metadata or {}).get("document_type", "legacy"),
            (metadata or {}).get("storage_path", ""),
            True,
        )
        services.audit_log_service.log_event(
            "DOCUMENT_DOWNLOADED",
            submission.get("submission_id", ""),
            slug,
            metadata={"filename": clean_filename, "signed": bool((metadata or {}).get("signed", False))},
        )
        raw_training_agreements = submission.get("training_agreements")
        if isinstance(raw_training_agreements, str):
            try:
                raw_training_agreements = json.loads(raw_training_agreements)
            except json.JSONDecodeError:
                raw_training_agreements = []
        matching_training_agreement = next(
            (
                item for item in (raw_training_agreements or [])
                if isinstance(item, dict) and str(item.get("filename") or "") == clean_filename
            ),
            None,
        )
        if (
            str((metadata or {}).get("document_type") or "") == SubmissionDocumentType.TRAINING_AGREEMENT
            or matching_training_agreement is not None
        ):
            agreement_key = str(
                (metadata or {}).get("training_key")
                or (matching_training_agreement or {}).get("id")
                or (matching_training_agreement or {}).get("training_id")
                or ""
            )
            if _mark_training_agreement_downloaded(
                str(submission.get("submission_id") or ""),
                agreement_key,
                clean_filename,
            ):
                services.audit_log_service.log_event(
                    "agreement_downloaded_by_beneficiary",
                    submission.get("submission_id", ""),
                    slug,
                    metadata={
                        "filename": clean_filename,
                        "training_key": agreement_key,
                    },
                )
    except HTTPException:
        raise
    except Exception:
        logger.warning(
            "Document download failed slug=%s filename=%s.",
            slug,
            Path(filename).name,
            exc_info=True,
        )
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
        services.submission_document_service.backfill_existing_legacy_documents(submission)
        metadata = services.submission_repository.get_file_metadata(
            submission.get("submission_id", ""), clean_filename, signed=True
        )
        allowed_types = {
            "",
            SubmissionDocumentType.SIGNED_FORM_PDF,
            SubmissionDocumentType.SIGNED_DECLARATION,
            SubmissionDocumentType.SIGNED_AGREEMENT,
            SubmissionDocumentType.SIGNED_TRAINING_AGREEMENT,
        }
        if metadata and str(metadata.get("document_type") or "") not in allowed_types:
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
        logger.info(
            "Signed document download public_submission_id=%s internal_submission_id=%s filename=%s "
            "document_type=%s storage_path=%s file_found=%s.",
            submission.get("submission_id", ""),
            submission.get("id", ""),
            clean_filename,
            (metadata or {}).get("document_type", "legacy"),
            (metadata or {}).get("storage_path", ""),
            True,
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
        logger.warning(
            "Signed document download failed slug=%s filename=%s.",
            slug,
            Path(filename).name,
            exc_info=True,
        )
        abort(404)
