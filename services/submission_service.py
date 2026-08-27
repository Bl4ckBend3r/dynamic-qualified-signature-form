from __future__ import annotations

import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from flask import current_app, has_request_context, request, url_for

from form_loader import (
    apply_pesel_derived_values,
    build_consents_view,
    build_submission_view,
    extract_submission_data,
    validate_submission,
)
from pdf_generator import generate_pdf
from services.access_token_service import AccessTokenService
from services.compliance_service import ComplianceService
from services.field_availability_service import FieldAvailabilityService
from services.document_naming_service import build_signed_submission_pdf_filename, build_submission_pdf_filename
from services.form_submission_mapper import FORM_FIELD_MAP, build_submission_from_form, validate_required_submission_fields
from services.process_service import OfficerDecision, ProcessStatus, build_initial_process_fields, build_legacy_process_fields, build_process_state
from services.qualification_condition_service import QualificationConditionService
from services.submission_document_service import SubmissionDocumentService, SubmissionDocumentType
from services.submission_attachment_service import SubmissionAttachmentService

logger = logging.getLogger(__name__)


class SubmissionService:
    def __init__(
        self,
        submission_repository,
        storage=None,
        workflow_service=None,
        document_service=None,
        notification_service=None,
        mail_dispatch_service=None,
        audit_log_service=None,
        access_token_service: AccessTokenService | None = None,
        submission_document_service: SubmissionDocumentService | None = None,
        qualification_condition_service: QualificationConditionService | None = None,
        compliance_service: ComplianceService | None = None,
        submission_attachment_service: SubmissionAttachmentService | None = None,
        submission_assignment_service=None,
        workflow_sla_service=None,
        validator=validate_submission,
    ) -> None:
        self.submission_repository = submission_repository
        self.storage = storage
        self.workflow_service = workflow_service
        self.document_service = document_service
        self.notification_service = notification_service
        self.mail_dispatch_service = mail_dispatch_service
        self.audit_log_service = audit_log_service
        self.access_token_service = access_token_service or AccessTokenService()
        self.submission_document_service = submission_document_service or SubmissionDocumentService(
            submission_repository=submission_repository,
            storage=storage,
        )
        self.qualification_condition_service = qualification_condition_service or QualificationConditionService()
        self.compliance_service = compliance_service or ComplianceService(submission_repository)
        self.submission_attachment_service = submission_attachment_service or SubmissionAttachmentService(
            submission_repository, storage
        )
        self.submission_assignment_service = submission_assignment_service
        self.workflow_sla_service = workflow_sla_service
        self.validator = validator

    def submit_form(
        self,
        form_slug: str,
        form_config: dict,
        request_form,
        *,
        form_version_id: int | None = None,
        submission_id: str | None = None,
    ) -> dict:
        submission_id = submission_id or str(uuid4())
        submission_data = extract_submission_data(form_config, request_form)
        submission_data = apply_pesel_derived_values(form_config, submission_data)
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
        attachments, attachment_errors = self.submission_attachment_service.validate_uploads(
            form_config,
            submission_data,
            request.files if has_request_context() else None,
        )
        errors.update(attachment_errors)
        mapped_errors = validate_required_submission_fields(mapped_submission, form_config)
        mapped_errors = self._map_errors_to_form_fields(mapped_errors, form_config)
        errors.update({key: value for key, value in mapped_errors.items() if key not in errors})
        consent_errors = self.compliance_service.validate_acceptances(form_config, submission_data)
        errors.update({key: value for key, value in consent_errors.items() if key not in errors})
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
                "data_json": {
                    **submission_data,
                    **(
                        {"_consent_snapshots": self.compliance_service.build_unversioned_snapshots(form_config, submission_data)}
                        if form_version_id is None
                        else {}
                    ),
                },
                "pdf_filename": "",
                "signed_pdf_filename": "",
                "signature_status": "manual",
                "signature_request_id": "mobywatel-manual",
                "form_version_id": form_version_id,
            },
            submission_id=submission_id,
            dispatch_received=False,
        )
        try:
            self.compliance_service.record_submission_acceptances(
                submission_id=submission_id,
                form_version_id=form_version_id,
                submission_data=submission_data,
                step=FieldAvailabilityService().initial_step(form_config),
            )
        except Exception:
            self.compliance_service.remove_incomplete_submission(submission_id)
            raise

        qualification = self.qualification_condition_service.evaluate(
            form_config.get("qualification_conditions"),
            submission_data,
            form_config.get("fields") or [],
        )
        self._persist_qualification_result(submission, submission_data, qualification)
        try:
            self.submission_attachment_service.persist(
                form_slug=form_slug,
                submission_id=submission_id,
                attachments=attachments,
                workflow_step=str((form_config.get("workflow") or {}).get("initial_step") or "submission"),
            )
        except Exception:
            self.compliance_service.remove_incomplete_submission(submission_id)
            raise
        failed_conditions = qualification.get("failed_conditions", [])

        auto_rejected = any(
            condition.get("failure_action") == "auto_reject"
            for condition in failed_conditions
        )

        requires_officer_decision = any(
            condition.get("failure_action") == "officer_decision"
            for condition in failed_conditions
        )

        if auto_rejected:
            self._auto_reject_submission(
                submission,
                qualification,
            )

        elif requires_officer_decision:
            self._mark_officer_decision_required(
                submission,
                qualification,
            )
        elif self.mail_dispatch_service:
            try:
                self.mail_dispatch_service.dispatch_submission_received(submission_id)
            except Exception as exc:
                current_app.logger.exception("submission_received_mail_failed error=%s", exc.__class__.__name__)

        self._generate_form_pdf(
            form_slug=form_slug,
            form_config=form_config,
            submission_id=submission_id,
            submission_data=submission_data,
            submission=submission,
            pdf_filename=pdf_filename,
        )

        result = {
            "submission_id": submission_id,
            "access_token": submission.get("access_token", ""),
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
            "auto_rejected": auto_rejected,
            "qualification_message": qualification.get("user_message") or (
                "Zgłoszenie zostało zapisane, ale nie spełnia warunków udziału."
                if auto_rejected
                else ""
            ),
            "can_continue": not auto_rejected,
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
        dispatch_received: bool = True,
        form_version_id: int | None = None,
    ) -> dict:
        submission_id = submission_id or str(uuid4())
        document_ids = self._enabled_document_ids(form_config)
        workflow = form_config.get("workflow") or {}
        initial_step = str(workflow.get("initial_step") or "submission")
        initial_step_config = next(
            (item for item in workflow.get("steps") or [] if isinstance(item, dict) and str(item.get("id") or "") == initial_step),
            {},
        )
        initial_status = str(initial_step_config.get("status") or ProcessStatus.FORM_SUBMITTED.value)
        submission = {
            "submission_id": submission_id,
            "form_slug": form_slug,
            "created_at": datetime.now().strftime("%d.%m.%Y"),
            "form_name": form_config.get("title", form_slug),
            "access_token": self.access_token_service.generate_token(),
            **form_data,
            "form_version_id": form_version_id if form_version_id is not None else form_data.get("form_version_id"),
            **build_initial_process_fields(
                declaration_required="declaration" in document_ids,
                agreement_required=bool({"agreement", "training_agreement"} & document_ids),
            ),
            **build_legacy_process_fields(),
            "process_status": initial_status,
            "workflow_step": initial_step,
            "workflow_stage": initial_step,
        }
        self.submission_repository.create(submission)
        if self.workflow_sla_service and hasattr(self.submission_repository, "session_factory"):
            self.workflow_sla_service.initialize_submission(
                self.submission_repository.session_factory,
                public_submission_id=submission_id,
                form_config=form_config,
            )
        if self.submission_assignment_service and hasattr(self.submission_repository, "session_factory"):
            try:
                self.submission_assignment_service.auto_assign_created(
                    self.submission_repository.session_factory,
                    submission_id=submission_id,
                    form_config=form_config,
                )
            except Exception:
                logger.exception("Automatyczny przydział zgłoszenia %s nie powiódł się; sprawa pozostaje w kolejce.", submission_id)
        logger.info("Zapis zgloszenia %s zakonczony sukcesem.", submission_id)
        if self.audit_log_service:
            self.audit_log_service.log_event("FORM_SUBMITTED", submission_id, form_slug)
        if dispatch_received and self.mail_dispatch_service:
            try:
                self.mail_dispatch_service.dispatch_submission_received(submission_id)
            except Exception as exc:
                current_app.logger.exception("submission_received_mail_failed error=%s", exc.__class__.__name__)
        return submission

    def submit_correction_form(
        self,
        form_slug: str,
        form_config: dict,
        request_form,
        *,
        submission_id: str,
        access_token: str,
    ) -> dict:
        existing = self.submission_repository.get_by_id(submission_id)
        if not existing or str(existing.get("form_slug") or "") != form_slug:
            return {"ok": False, "errors": {"submission_id": "Nie znaleziono zgłoszenia."}, "values": {}, "result": None}
        if not self.access_token_service.verify_token(existing, access_token):
            return {"ok": False, "errors": {"submission_id": "Nieprawidłowy link do poprawy."}, "values": {}, "result": None}
        if str(existing.get("process_status") or "") != ProcessStatus.RETURNED_FOR_CORRECTION.value:
            return {
                "ok": False,
                "errors": {"submission_id": "Zgłoszenie nie oczekuje na ponowne uzupełnienie."},
                "values": {},
                "result": None,
            }

        submission_data = apply_pesel_derived_values(
            form_config,
            extract_submission_data(form_config, request_form),
        )
        mapped_submission = build_submission_from_form(submission_data, form_config)
        errors = self._validate(form_config, submission_data)
        attachments, attachment_errors = self.submission_attachment_service.validate_uploads(
            form_config,
            submission_data,
            request.files if has_request_context() else None,
            submission_id=submission_id,
        )
        errors.update(attachment_errors)
        mapped_errors = validate_required_submission_fields(mapped_submission, form_config)
        mapped_errors = self._map_errors_to_form_fields(mapped_errors, form_config)
        errors.update({key: value for key, value in mapped_errors.items() if key not in errors})
        if errors:
            return {"ok": False, "errors": errors, "values": submission_data, "result": None}

        qualification = self.qualification_condition_service.evaluate(
            form_config.get("qualification_conditions"),
            submission_data,
            form_config.get("fields") or [],
        )
        system_data = {
            key: value
            for key, value in dict(existing.get("data_json") or {}).items()
            if str(key).startswith("_")
        }
        history = list(system_data.get("_qualification_history") or [])
        history.append(qualification)
        stored_data = {
            **submission_data,
            **system_data,
            "_qualification": qualification,
            "_qualification_history": history,
        }
        updates = {
            **submission_data,
            "data_json": stored_data,
            "process_status": ProcessStatus.FORM_SUBMITTED.value,
            "workflow_step": str((form_config.get("workflow") or {}).get("initial_step") or "submission"),
            "correction_required": "Nie",
            "correction_completed_at": datetime.now(timezone.utc).isoformat(),
            "officer_decision": "",
            "officer_decision_reason": "",
        }
        self.submission_repository.update(submission_id, updates)
        self.submission_attachment_service.persist(
            form_slug=form_slug,
            submission_id=submission_id,
            attachments=attachments,
            workflow_step=str(existing.get("workflow_step") or "returned_for_correction"),
        )
        refreshed = {**existing, **updates}
        failed_conditions = qualification.get(
            "failed_conditions",
            [],
        )

        auto_rejected = any(
            condition.get("failure_action") == "auto_reject"
            for condition in failed_conditions
        )

        requires_officer_decision = any(
            condition.get("failure_action") == "officer_decision"
            for condition in failed_conditions
        )
        blocked_by_condition = (
            auto_rejected
            or requires_officer_decision
            or any(
                bool(condition.get("cancel_process_on_failure"))
                for condition in failed_conditions
            )
        )

        if auto_rejected:
            self._auto_reject_submission(
                refreshed,
                qualification,
                previous_status=ProcessStatus.RETURNED_FOR_CORRECTION.value,
                previous_step="returned_for_correction",
            )

        elif requires_officer_decision:
            self._mark_officer_decision_required(
                refreshed,
                qualification,
                previous_status=ProcessStatus.RETURNED_FOR_CORRECTION.value,
                previous_step="returned_for_correction",
            )

        else:
            self.submission_repository.record_workflow_event(
                submission_id,
                {
                    "previous_status": ProcessStatus.RETURNED_FOR_CORRECTION.value,
                    "new_status": ProcessStatus.FORM_SUBMITTED.value,
                    "previous_step": "returned_for_correction",
                    "new_step": updates["workflow_step"],
                    "actor_role": "participant",
                    "reason": "correction_resubmitted",
                    "source": "correction_resubmitted",
                },
            )
            if self.audit_log_service:
                self.audit_log_service.log_event(
                    "CORRECTION_RESUBMITTED",
                    submission_id,
                    form_slug,
                    old_value=ProcessStatus.RETURNED_FOR_CORRECTION.value,
                    new_value=ProcessStatus.FORM_SUBMITTED.value,
                )
            if self.mail_dispatch_service:
                try:
                    self.mail_dispatch_service.dispatch_submission_received(submission_id)
                except Exception as exc:
                    current_app.logger.exception("correction_received_mail_failed error=%s", exc.__class__.__name__)

        self.storage.ensure_form_output_structure(form_slug)
        base_filename = Path(self.build_pdf_filename(form_slug, submission_id))
        pdf_filename = f"{base_filename.stem}-korekta-{uuid4().hex[:8]}{base_filename.suffix}"
        self._generate_form_pdf(
            form_slug=form_slug,
            form_config=form_config,
            submission_id=submission_id,
            submission_data=submission_data,
            submission=refreshed,
            pdf_filename=pdf_filename,
        )
        return {
            "ok": True,
            "errors": {},
            "values": submission_data,
            "submission": refreshed,
            "result": {
                "submission_id": submission_id,
                "access_token": refreshed.get("access_token", ""),
                "form_slug": form_slug,
                "form_title": form_config.get("title", form_slug),
                "pdf_filename": pdf_filename,
                "pdf_url": self.document_service.build_download_url(refreshed, pdf_filename) if self.document_service else "",
                "upload_url": url_for("documents.upload_signed_pdf", slug=form_slug, submission_id=submission_id),
                "signed_pdf_url": None,
                "verification": None,
                "auto_rejected": auto_rejected,
                "qualification_message": qualification.get("user_message") or (
                    "Zgłoszenie zostało zapisane, ale nadal nie spełnia warunków udziału."
                    if auto_rejected
                    else "Zgłoszenie poprawiono i przesłano ponownie."
                ),
                "can_continue": not blocked_by_condition,
            },
        }

    def _generate_form_pdf(
        self,
        *,
        form_slug: str,
        form_config: dict,
        submission_id: str,
        submission_data: dict,
        submission: dict,
        pdf_filename: str,
    ) -> None:
        pdf_context = {
            "form_definition": form_config,
            "submission_view": build_submission_view(form_config, submission_data),
            "submission_id": submission_id,
            "pdf_image_url": self._resolve_pdf_image_url(form_config),
            "pdf_image_alt": form_config.get("title", ""),
            "consents_view": build_consents_view(form_config, submission_data),
        }
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=current_app.config["TEMP_DIR"]) as tmp_pdf:
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
        self.submission_document_service.record_generated_document(
            submission_id=submission_id,
            form_slug=form_slug,
            filename=pdf_filename,
            file_bytes=pdf_bytes,
            document_id="form_submission",
            document_type=SubmissionDocumentType.FORM_PDF,
            storage=self.storage,
        )
        if self.audit_log_service:
            self.audit_log_service.log_event(
                "PDF_GENERATED", submission_id, form_slug, metadata={"filename": pdf_filename}
            )

    def _persist_qualification_result(self, submission: dict, submission_data: dict, evaluation: dict) -> None:
        previous_data = submission.get("data_json") if isinstance(submission.get("data_json"), dict) else {}
        system_data = {key: value for key, value in previous_data.items() if str(key).startswith("_")}
        history = list(previous_data.get("_qualification_history") or [])
        history.append(evaluation)
        stored_data = {**submission_data, **system_data}
        stored_data["_qualification"] = evaluation
        stored_data["_qualification_history"] = history
        self.submission_repository.update(submission["submission_id"], {"data_json": stored_data})
        submission["data_json"] = stored_data

    def _mark_officer_decision_required(
        self,
        submission: dict,
        evaluation: dict,
        *,
        previous_status: str = ProcessStatus.FORM_SUBMITTED.value,
        previous_step: str | None = None,
    ) -> None:
        failed = [
            item
            for item in (evaluation.get("failed_conditions") or [])
            if item.get("failure_action") == "officer_decision"
        ]

        details = []

        for item in failed:
            label = str(
                item.get("field_label")
                or item.get("field_name")
                or "warunek"
            )

            details.append(
                f"{label}: "
                f"wartość użytkownika={item.get('actual_value')!r}, "
                f"wartość oczekiwana={item.get('expected_value')!r}"
            )

        officer_messages = [
            str(item.get("officer_message") or "").strip()
            for item in failed
            if str(item.get("officer_message") or "").strip()
        ]

        reason = (
            "Zgłoszenie wymaga decyzji urzędnika z powodu "
            "niespełnienia warunku: "
            + "; ".join(details)
        )

        if officer_messages:
            reason += (
                ". Informacja dla urzędnika: "
                + " ".join(officer_messages)
            )

        previous_step = str(
            previous_step
            or submission.get("workflow_step")
            or "submission"
        )

        updates = {
            "process_status": ProcessStatus.FORM_SUBMITTED.value,
            "workflow_step": "officer_decision",
            "officer_decision": "",
            "officer_decision_reason": reason,
        }

        self.submission_repository.update(
            submission["submission_id"],
            updates,
        )

        submission.update(updates)

        self.submission_repository.record_workflow_event(
            submission["submission_id"],
            {
                "previous_status": previous_status,
                "new_status": ProcessStatus.FORM_SUBMITTED.value,
                "previous_step": previous_step,
                "new_step": "officer_decision",
                "actor_role": "system",
                "reason": reason,
                "source": "qualification_officer_decision",
            },
        )

        if self.audit_log_service:
            self.audit_log_service.log_event(
                "QUALIFICATION_OFFICER_DECISION_REQUIRED",
                submission["submission_id"],
                submission.get("form_slug", ""),
                old_value=previous_status,
                new_value=ProcessStatus.FORM_SUBMITTED.value,
                metadata={
                    "failed_conditions": [
                        {
                            "id": item.get("id"),
                            "field_name": item.get("field_name"),
                            "actual_value": item.get("actual_value"),
                            "expected_value": item.get("expected_value"),
                            "officer_message": item.get("officer_message"),
                        }
                        for item in failed
                    ],
                },
            )
    
    def _auto_reject_submission(
        self,
        submission: dict,
        evaluation: dict,
        *,
        previous_status: str = ProcessStatus.FORM_SUBMITTED.value,
        previous_step: str | None = None,
    ) -> None:
        failed = evaluation.get("failed_conditions") or []
        labels = [str(item.get("field_label") or item.get("field_name") or "warunek") for item in failed]
        officer_messages = [str(item.get("officer_message") or "").strip() for item in failed]
        condition_details = []
        for item, label in zip(failed, labels):
            condition_details.append(
                f"{label}: wartość użytkownika={item.get('actual_value')!r}, "
                f"wartość oczekiwana={item.get('expected_value')!r}"
            )
        reason = "Zgłoszenie zostało automatycznie odrzucone, ponieważ nie spełnia warunku: " + "; ".join(condition_details)
        if any(officer_messages):
            reason += ". Informacja dla urzędnika: " + " ".join(message for message in officer_messages if message)
        previous_step = str(previous_step or submission.get("workflow_step") or "submission")
        updates = {
            "process_status": ProcessStatus.AUTO_REJECTED.value,
            "workflow_step": "auto_rejected",
            "officer_decision": "",
            "officer_decision_reason": reason,
        }
        self.submission_repository.update(submission["submission_id"], updates)
        submission.update(updates)
        self.submission_repository.record_workflow_event(
            submission["submission_id"],
            {
                "previous_status": previous_status,
                "new_status": ProcessStatus.AUTO_REJECTED.value,
                "previous_step": previous_step,
                "new_step": "auto_rejected",
                "actor_role": "system",
                "reason": reason,
                "source": "auto_rejected_by_condition",
            },
        )
        if self.audit_log_service:
            self.audit_log_service.log_event(
                "AUTO_REJECTED_BY_CONDITION",
                submission["submission_id"],
                submission.get("form_slug", ""),
                old_value=previous_status,
                new_value=ProcessStatus.AUTO_REJECTED.value,
                metadata={
                    "failed_conditions": [
                        {
                            "id": item.get("id"),
                            "field_name": item.get("field_name"),
                            "actual_value": item.get("actual_value"),
                            "expected_value": item.get("expected_value"),
                            "officer_message": item.get("officer_message"),
                        }
                        for item in failed
                    ]
                },
            )
        if self.mail_dispatch_service:
            try:
                self.mail_dispatch_service.dispatch_auto_rejected_by_condition(
                    submission["submission_id"],
                    evaluation,
                )
            except Exception as exc:
                current_app.logger.exception("auto_rejected_mail_failed error=%s", exc.__class__.__name__)

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
        process_state = build_process_state(row)
        can_view_status_details = process_state.officer_decision == OfficerDecision.ACCEPTED or process_state.agreement_blocked
        return {
            "submission_id": submission_id,
            "form_slug": form_slug,
            "form_title": form_title,
            "officer_decision": process_state.officer_decision.value,
            "process_status": str(row.get("process_status") or process_state.status.value),
            "can_sign_documents": process_state.can_sign_documents,
            "can_view_status_details": can_view_status_details,
            "workflow_step": str(row.get("workflow_step") or "").strip(),
            "row": row,
        }

    def build_pdf_filename(self, slug: str, submission_id: str) -> str:
        return build_submission_pdf_filename(slug, submission_id)

    def build_signed_pdf_filename(self, slug: str, submission_id: str) -> str:
        return build_signed_submission_pdf_filename(slug, submission_id)

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

    @staticmethod
    def _map_errors_to_form_fields(errors: dict[str, str], form_config: dict) -> dict[str, str]:
        column_to_field = {}
        for field in form_config.get("fields", []):
            field_name = str(field.get("name") or "")
            if field_name:
                column_to_field.setdefault(FORM_FIELD_MAP.get(field_name, field_name), field_name)
        return {column_to_field.get(name, name): message for name, message in errors.items()}
