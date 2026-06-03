from __future__ import annotations

import tempfile
import logging
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from flask import current_app, request, url_for

from form_loader import (
    build_consents_view,
    build_submission_view,
    extract_submission_data,
    validate_submission,
)
from pdf_generator import generate_pdf
from services.access_token_service import AccessTokenService
from services.file_metadata import record_submission_file
from services.form_submission_mapper import build_submission_from_form, validate_required_submission_fields
from services.process_service import build_initial_process_fields, build_legacy_process_fields

logger = logging.getLogger(__name__)


class SubmissionService:
    def __init__(
        self,
        submission_repository,
        storage=None,
        workflow_service=None,
        document_service=None,
        notification_service=None,
        audit_log_service=None,
        access_token_service: AccessTokenService | None = None,
        validator=validate_submission,
    ) -> None:
        self.submission_repository = submission_repository
        self.storage = storage
        self.workflow_service = workflow_service
        self.document_service = document_service
        self.notification_service = notification_service
        self.audit_log_service = audit_log_service
        self.access_token_service = access_token_service or AccessTokenService()
        self.validator = validator

    def submit_form(self, form_slug: str, form_config: dict, request_form) -> dict:
        submission_id = str(uuid4())
        submission_data = extract_submission_data(form_config, request_form)
        mapped_submission, map_meta = build_submission_from_form(
            submission_data,
            form_config,
            include_metadata=True,
        )
        logger.info(
            "Mapowanie formularza %s: pola zapisane=%s; pola pominiete=%s.",
            form_slug,
            ", ".join(map_meta["saved_fields"]) or "-",
            ", ".join(map_meta["skipped_fields"]) or "-",
        )

        errors = self._validate(form_config, submission_data)
        mapped_errors = validate_required_submission_fields(mapped_submission, form_config)
        errors.update({key: value for key, value in mapped_errors.items() if key not in errors})
        if errors:
            return {
                "ok": False,
                "errors": errors,
                "values": submission_data,
                "submission": None,
                "result": None,
            }

        self.storage.ensure_form_output_structure(form_slug)
        pdf_filename = self.build_pdf_filename(form_slug, submission_id)
        submission = self.create_submission(
            form_slug,
            form_config,
            {
                **submission_data,
                "data_json": submission_data,
                "pdf_filename": "",
                "signed_pdf_filename": "",
                "signature_status": "manual",
                "signature_request_id": "mobywatel-manual",
            },
            submission_id=submission_id,
        )

        pdf_context = {
            "form_definition": form_config,
            "submission_view": build_submission_view(form_config, submission_data),
            "submission_id": submission_id,
            "pdf_image_url": self._resolve_pdf_image_url(form_config),
            "pdf_image_alt": form_config.get("title", ""),
            "consents_view": build_consents_view(form_config, submission_data),
        }
        with tempfile.NamedTemporaryFile(
            suffix=".pdf",
            delete=False,
            dir=current_app.config["TEMP_DIR"],
        ) as tmp_pdf:
            tmp_pdf_path = Path(tmp_pdf.name)

        try:
            generate_pdf(
                app=current_app._get_current_object(),
                template_name="pdf_template.html",
                context=pdf_context,
                output_path=tmp_pdf_path,
            )
            pdf_bytes = tmp_pdf_path.read_bytes()
            self.storage.save_pdf(form_slug, pdf_filename, pdf_bytes)
            logger.info("Upload PDF do Nextcloud zakonczony sukcesem: %s", pdf_filename)
        finally:
            tmp_pdf_path.unlink(missing_ok=True)

        self.submission_repository.update(submission_id, {"pdf_filename": pdf_filename})
        submission["pdf_filename"] = pdf_filename
        record_submission_file(
            submission_repository=self.submission_repository,
            submission_id=submission_id,
            form_slug=form_slug,
            filename=pdf_filename,
            storage=self.storage,
            file_bytes=pdf_bytes,
            document_id="form_submission",
            document_type="",
            signed=False,
        )
        if self.audit_log_service:
            self.audit_log_service.log_event("PDF_GENERATED", submission_id, form_slug, metadata={"filename": pdf_filename})

        result = {
            "submission_id": submission_id,
            "form_slug": form_slug,
            "pdf_filename": pdf_filename,
            "pdf_url": self.document_service.build_download_url(submission, pdf_filename) if self.document_service else "",
            "signature_request_id": "mobywatel-manual",
            "signature_status": "manual",
            "signed_pdf_filename": "",
            "signed_pdf_url": None,
            "upload_url": url_for("documents.upload_signed_pdf", slug=form_slug, submission_id=submission_id),
            "form_title": form_config["title"],
            "verification": None,
        }
        return {
            "ok": True,
            "errors": {},
            "values": submission_data,
            "submission": submission,
            "result": result,
        }

    def create_submission(
        self,
        form_slug: str,
        form_config: dict,
        form_data: dict,
        submission_id: str | None = None,
    ) -> dict:
        submission_id = submission_id or str(uuid4())
        document_ids = self._enabled_document_ids(form_config)
        submission = {
            "submission_id": submission_id,
            "form_slug": form_slug,
            "created_at": datetime.now().strftime("%d.%m.%Y"),
            "form_name": form_config.get("title", form_slug),
            "access_token": self.access_token_service.generate_token(),
            **form_data,
            **build_initial_process_fields(
                declaration_required="declaration" in document_ids,
                agreement_required=bool({"agreement", "training_agreement"} & document_ids),
            ),
            **build_legacy_process_fields(),
        }
        self.submission_repository.create(submission)
        logger.info("Zapis zgloszenia %s zakonczony sukcesem.", submission_id)
        if self.audit_log_service:
            self.audit_log_service.log_event("FORM_SUBMITTED", submission_id, form_slug)
        if self.notification_service:
            try:
                self.notification_service.notify_event("FORM_SUBMITTED", submission, form_config)
            except Exception as exc:
                current_app.logger.exception("Nie udało się wysłać powiadomienia FORM_SUBMITTED: %s", exc)
        return submission

    def get_submission_context(self, submission_id: str, form_config_service=None, storage=None) -> dict | None:
        row = self.submission_repository.get_by_id(submission_id)
        if not row:
            return None
        form_slug = str(row.get("form_slug") or "").strip()
        form_title = row.get("form_name") or form_slug
        if form_config_service and storage and form_slug:
            meta = form_config_service.get_form_meta(storage, form_slug)
            if meta:
                form_title = row.get("form_name") or meta.get("title") or form_slug
        from services.process_service import build_process_state

        process_state = build_process_state(row)
        return {
            "submission_id": submission_id,
            "form_slug": form_slug,
            "form_title": form_title,
            "officer_decision": process_state.officer_decision.value,
            "process_status": process_state.status.value,
            "can_sign_documents": process_state.can_sign_documents,
            "row": row,
        }

    def build_pdf_filename(self, slug: str, submission_id: str) -> str:
        return f"{slug}-{submission_id}.pdf"

    def build_signed_pdf_filename(self, slug: str, submission_id: str) -> str:
        return f"{slug}-{submission_id}-signed.pdf"

    def _enabled_document_ids(self, form_config: dict) -> set[str]:
        documents = form_config.get("documents", [])
        if isinstance(documents, dict):
            return {
                document_id
                for document_id, document in documents.items()
                if not isinstance(document, dict) or document.get("enabled", True)
            }
        return {document.get("id") for document in documents if document.get("enabled", True)}

    def _resolve_pdf_image_url(self, form_config: dict) -> str | None:
        image_value = form_config.get("header_image") or form_config.get("logo_url")
        if not image_value:
            return None
        normalized = str(image_value).replace("\\", "/").lstrip("/")
        if normalized.startswith(("http://", "https://")):
            return normalized
        if normalized.startswith("static/"):
            normalized = normalized[len("static/"):]
        if normalized.startswith("assets/"):
            return request.url_root.rstrip("/") + "/" + normalized
        return request.url_root.rstrip("/") + "/static/" + normalized

    def _validate(self, form_config: dict, submission_data: dict) -> dict:
        try:
            import app as app_module
        except Exception:
            return self.validator(form_config, submission_data)
        validator = getattr(app_module, "validate_submission", self.validator)
        return validator(form_config, submission_data)
