from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Callable, Mapping

from services.process_service import ProcessStatus
from services.status_catalog import build_status_view
from services.submission_document_service import SubmissionDocumentType
from services.public_submission_status_service import build_public_submission_status


logger = logging.getLogger(__name__)

_AGREEMENT_STATUS_LABELS = {
    "selected": "Umowa do wygenerowania",
    "agreement_generated": "Umowa wygenerowana",
    "agreement_downloaded": "Umowa pobrana",
    "agreement_waiting_for_beneficiary_signature": "Umowa oczekuje na podpis beneficjenta",
    "agreement_uploaded_by_beneficiary": "Podpisana umowa wgrana",
    "agreement_waiting_for_office_signature": "Umowa oczekuje na podpis urzędu",
    "agreement_signed_by_office": "Umowa podpisana przez urząd",
    "locked": "Umowa zablokowana",
}


def _agreement_status_label(status: str) -> str:
    return _AGREEMENT_STATUS_LABELS.get(str(status or ""), "Status umowy niedostępny")


class DocumentViewService:
    def build_documents_view(
        self,
        *,
        row: Mapping[str, Any],
        documents_config: list[dict],
        download_url_builder: Callable[[str, bool], str],
        available_actions: list[dict] | None = None,
        document_files: list[Mapping[str, Any]] | None = None,
        allow_legacy_fallback: bool = True,
    ) -> dict:
        documents = []
        files_by_type = self._files_by_type(document_files or [])
        for document in documents_config:
            document_id = str(document.get("id") or "")
            metadata = self._metadata_for_document(files_by_type, document_id, signed=False)
            signed_metadata = self._metadata_for_document(files_by_type, document_id, signed=True)
            used_legacy_fallback = metadata is None
            filename = str((metadata or {}).get("filename") or "").strip()
            if not filename and allow_legacy_fallback:
                filename = self.document_filename(row, document_id)
                used_legacy_fallback = True
            signature_valid = self.document_signature_valid(row, document_id)
            if signed_metadata:
                signature_valid = bool(signed_metadata.get("signed")) and str(
                    signed_metadata.get("signature_status") or signed_metadata.get("status") or ""
                ).strip().lower() in {"tak", "valid", "signed"}
            if used_legacy_fallback and filename:
                logger.warning("Legacy document view fallback used for document_id=%s filename=%s.", document_id, filename)
            documents.append(
                {
                    "id": document.get("id"),
                    "label": document.get("label"),
                    "filename": filename,
                    "url": download_url_builder(filename, False) if filename else "",
                    "signature_required": document.get("signature_required", True),
                    "signature_valid": signature_valid,
                    "signature_status": (signed_metadata or metadata or {}).get("signature_status", ""),
                    "signature_validation_result": (signed_metadata or metadata or {}).get("signature_validation_result") or {},
                    "generated_at": (metadata or {}).get("generated_at"),
                    "signed_at": (signed_metadata or {}).get("signed_at"),
                    "agreement_number": (metadata or signed_metadata or {}).get("agreement_number", ""),
                    "training_key": (metadata or signed_metadata or {}).get("training_key", ""),
                    "source": "legacy" if used_legacy_fallback else "submission_file",
                    "used_legacy_fallback": used_legacy_fallback,
                    "can_download": bool(filename),
                    "can_upload": bool(filename) and not signature_valid,
                    "actions": [],
                }
            )

        status_view = build_status_view(str(row.get("process_status") or ""))
        return {
            "current_step": row.get("workflow_step") or "",
            "available_actions": available_actions or [],
            "documents": documents,
            **status_view,
        }

    @staticmethod
    def document_filename(row: Mapping[str, Any], document_id: str) -> str:
        if document_id == "declaration":
            return str(row.get("declaration_filename") or "").strip()
        if document_id in {"agreement", "training_agreement"}:
            return str(row.get("agreement_filename") or "").strip()
        return str(row.get(f"{document_id}_filename") or "").strip()

    @staticmethod
    def document_signature_valid(row: Mapping[str, Any], document_id: str) -> bool:
        if document_id == "declaration":
            return str(row.get("declaration_signature_valid") or "").strip().lower() == "tak"
        if document_id in {"agreement", "training_agreement"}:
            return str(row.get("agreement_signature_valid") or "").strip().lower() == "tak"
        return str(row.get(f"{document_id}_signature_valid") or "").strip().lower() == "tak"

    def _files_by_type(self, document_files: list[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for file_row in document_files:
            grouped.setdefault(str(file_row.get("document_type") or ""), []).append(file_row)
        return grouped

    def _metadata_for_document(
        self,
        files_by_type: dict[str, list[Mapping[str, Any]]],
        document_id: str,
        *,
        signed: bool,
    ) -> Mapping[str, Any] | None:
        document_type = self._document_type_for_view(document_id, signed=signed)
        rows = files_by_type.get(document_type) or []
        return rows[-1] if rows else None

    def _document_type_for_view(self, document_id: str, *, signed: bool) -> str:
        if document_id == "declaration":
            return SubmissionDocumentType.SIGNED_DECLARATION if signed else SubmissionDocumentType.DECLARATION
        if document_id == "training_agreement":
            return (
                SubmissionDocumentType.SIGNED_TRAINING_AGREEMENT
                if signed
                else SubmissionDocumentType.TRAINING_AGREEMENT
            )
        if document_id == "agreement":
            return SubmissionDocumentType.SIGNED_AGREEMENT if signed else SubmissionDocumentType.AGREEMENT
        return SubmissionDocumentType.SIGNED_FORM_PDF if signed else SubmissionDocumentType.FORM_PDF

    def build_additional_fields_result(
        self,
        *,
        submission_id: str,
        submission: dict,
        form_config: dict,
        additional_definition: dict,
        additional_action_url: str,
        status_labeler: Callable[[str, dict], str],
        additional_errors: dict | None = None,
        additional_values: dict | None = None,
    ) -> dict:
        row = submission["row"]
        raw_status = row.get("process_status") or ProcessStatus.ACCEPTED_WAITING_FOR_ADDITIONAL_FIELDS.value
        status_view = build_status_view(raw_status)
        return {
            "submission_id": submission_id,
            "form_slug": submission["form_slug"],
            "form_title": submission["form_title"],
            "message": "Wniosek zostal zaakceptowany. Uzupelnij dodatkowe informacje, aby pobrac deklaracje.",
            "process_status": raw_status,
            "current_status": status_view["current_status"],
            "process_status_label": status_labeler(raw_status, form_config),
            "is_final": status_view["is_final"],
            "is_rejected": status_view["is_rejected"],
            "requires_user_action": status_view["requires_user_action"],
            "requires_officer_action": status_view["requires_officer_action"],
            "can_upload": False,
            "can_download": False,
            "visible_steps": ["additional_fields"],
            "visible_actions": ["save_additional_fields"],
            "needs_additional_fields": True,
            "additional_form_definition": additional_definition,
            "additional_action_url": additional_action_url,
            "additional_errors": additional_errors or {},
            "additional_values": additional_values or row,
            "declaration_filename": "",
            "declaration_url": None,
            "declaration_upload_url": None,
            "declaration_signature_valid": False,
            "agreement_generated": False,
            "training_agreements": [],
            "workflow": {"current_step": row.get("workflow_step") or "", "available_actions": [], "documents": []},
            "available_actions": [],
        }

    def build_documents_to_sign_result(
        self,
        *,
        submission_id: str,
        submission: dict,
        form_config: dict,
        declaration: dict,
        process_state,
        current_step: str,
        available_actions: list[dict],
        documents_view: dict,
        download_url_builder: Callable[[str, bool], str],
        declaration_upload_url: str | None,
        generate_agreement_url: str,
        agreement_upload_url_builder: Callable[[str], str],
        agreement_upload_url: str,
        agreement_required: bool,
        agreement_template_configured: bool,
        status_labeler: Callable[[str, dict], str],
        available_filenames: set[str] | None = None,
    ) -> dict:
        row = submission["row"]
        status_view = build_status_view(process_state.status.value)
        public_status = build_public_submission_status(row)
        training_agreements = _parse_json_list(row.get("training_agreements"))
        selected_trainings = _parse_json_list(row.get("selected_trainings"))
        def training_key(item: Mapping[str, Any]) -> str:
            nested = item.get("training") if isinstance(item.get("training"), Mapping) else {}
            return str(nested.get("id") or item.get("training_id") or item.get("id") or "").strip()

        inactive_statuses = {
            "unselected", "cancelled", "cancelled_before_signed_agreement",
            "rejected", "inactive_without_signed_agreement",
        }
        selected_training_ids = {training_key(item) for item in selected_trainings if training_key(item)}
        raw_selection = row.get("selected_trainings")
        selection_snapshot_present = raw_selection is not None and str(raw_selection).strip() not in {"", "null", "None"}
        training_agreements = [
            agreement for agreement in training_agreements
            if str(agreement.get("participant_status") or "") not in inactive_statuses
            and (
                not selection_snapshot_present
                or training_key(agreement) in selected_training_ids
                or bool(agreement.get("is_locked") or agreement.get("signature_valid"))
            )
        ]

        generated_training_ids = {
            training_key(item)
            for item in training_agreements
            if training_key(item) and str(item.get("filename") or "").strip()
        }
        pending_trainings = [
            item for item in selected_trainings
            if training_key(item) and training_key(item) not in generated_training_ids
        ]
        agreement_actions_allowed = bool(
            public_status["declaration_completed"]
            and str(process_state.status.value) not in {
                ProcessStatus.AUTO_REJECTED.value,
                ProcessStatus.OFFICER_REJECTED.value,
                ProcessStatus.PARTICIPANT_REJECTED.value,
                ProcessStatus.RETURNED_FOR_CORRECTION.value,
                ProcessStatus.AGREEMENT_REJECTED_BY_OFFICE.value,
                ProcessStatus.BENEFICIARY_AGREEMENT_REJECTED.value,
            }
        )
        today_iso = date.today().isoformat()
        declaration_enabled = bool(declaration.get("enabled"))
        declaration_filename = declaration.get("filename", "")
        declaration_ready = bool(declaration_enabled and declaration_filename)
        available = available_filenames

        def is_available(filename: str) -> bool:
            return bool(filename) and (available is None or filename in available)

        training_agreement_views = []
        if agreement_template_configured:
            for agreement in training_agreements:
                status = str(agreement.get("participant_status") or "agreement_generated")
                office_signed = status == "agreement_signed_by_office"
                beneficiary_uploaded = bool(
                    agreement.get("signature_valid")
                    or agreement.get("is_locked")
                    or status in {
                        "agreement_uploaded_by_beneficiary",
                        "agreement_waiting_for_office_signature",
                        "agreement_signed_by_office",
                    }
                )
                downloaded = bool(
                    agreement.get("agreement_downloaded")
                    or status in {"agreement_downloaded", "agreement_waiting_for_beneficiary_signature"}
                )
                if office_signed:
                    title = "Umowa została podpisana przez urząd"
                    description = "Finalna umowa jest dostępna do pobrania."
                elif beneficiary_uploaded:
                    title = "Podpisana umowa została wgrana"
                    description = "Szkolenie zostało zablokowane. Umowa oczekuje na podpis i potwierdzenie po stronie urzędu."
                elif downloaded:
                    title = "Umowa oczekuje na podpis"
                    description = "Podpisz pobraną umowę i wgraj podpisany plik."
                else:
                    title = "Umowa do podpisania"
                    description = "Pobierz umowę, podpisz ją i wgraj podpisany plik PDF."
                filename = str(agreement.get("filename") or "")
                signed_filename = str(agreement.get("signed_filename") or "")
                final_filename = str(agreement.get("office_signed_filename") or "")
                agreement_view = {
                    **agreement,
                    "state": status,
                    "status_label": str(agreement.get("participant_status_label") or _agreement_status_label(status)),
                    "state_title": title,
                    "state_description": description,
                    "beneficiary_uploaded": beneficiary_uploaded,
                    "office_signed": office_signed,
                    "downloaded": downloaded,
                    "can_upload": bool(
                        agreement_actions_allowed
                        and
                        not beneficiary_uploaded
                        and filename
                        and status in {
                            "agreement_downloaded",
                            "agreement_waiting_for_beneficiary_signature",
                        }
                    ),
                    "download_label": "Pobierz ponownie umowę PDF" if downloaded else "Pobierz umowę PDF",
                    "url": download_url_builder(filename, False) if is_available(filename) else "",
                    "signed_url": download_url_builder(signed_filename, True) if signed_filename else "",
                    "final_url": download_url_builder(final_filename, True) if final_filename else "",
                    "upload_url": agreement_upload_url_builder(agreement.get("id", "")),
                }
                training_agreement_views.append(agreement_view)
            for training in pending_trainings:
                key = training_key(training)
                training_agreement_views.append({
                    "id": key,
                    "training_id": key,
                    "training_name": str(training.get("name") or key),
                    "number": "",
                    "filename": "",
                    "state": "selected",
                    "status_label": _agreement_status_label("selected"),
                    "state_title": "Umowa do wygenerowania",
                    "state_description": "Dla tego szkolenia nie wygenerowano jeszcze umowy.",
                    "participant_status_label": "Wybrane",
                    "beneficiary_uploaded": False,
                    "office_signed": False,
                    "downloaded": False,
                    "can_upload": False,
                    "can_generate": agreement_actions_allowed,
                    "is_pending_generation": True,
                    "is_locked": False,
                    "url": "",
                    "signed_url": "",
                    "final_url": "",
                    "upload_url": "",
                })

        return {
            **public_status,
            "submission_id": submission_id,
            "form_slug": submission["form_slug"],
            "form_title": submission["form_title"],
            "message": public_status["status_description"],
            "status_title": public_status["status_title"],
            "process_status": public_status["effective_process_status"],
            "current_status": status_view["current_status"],
            "process_status_label": status_labeler(process_state.status.value, form_config),
            "is_final": status_view["is_final"],
            "is_rejected": status_view["is_rejected"],
            "requires_user_action": status_view["requires_user_action"],
            "requires_officer_action": status_view["requires_officer_action"],
            "can_upload": bool(
                public_status["can_upload_signed_declaration"]
                or public_status["can_upload_signed_agreement"]
            ),
            "can_download": bool(
                public_status["can_download_declaration"]
                or public_status["can_download_agreement"]
            ),
            "visible_steps": [
                step
                for step, visible in {
                    "declaration": declaration_enabled,
                    "agreement": agreement_template_configured
                    and (bool(pending_trainings) or bool(training_agreements)),
                }.items()
                if visible
            ],
            "visible_actions": [action.get("id") for action in available_actions if action.get("id")],
            "workflow": {
                "current_step": current_step,
                "available_actions": available_actions,
                "documents": documents_view["documents"],
            },
            "available_actions": available_actions,
            "declaration_filename": declaration_filename,
            "declaration_url": (
                download_url_builder(declaration_filename, False)
                if declaration_ready
                else None
            ),
            "declaration_upload_url": declaration_upload_url if declaration_enabled else None,
            "declaration_signature_valid": str(row.get("declaration_signature_valid", "")).strip().lower() == "tak",
            "agreement_blocked": public_status["agreement_blocked"],
            "agreement_block_reason": public_status["blocking_reason"],
            "can_generate_agreement": bool(
                agreement_template_configured
                and agreement_actions_allowed
                and pending_trainings
            ),
            "generate_agreement_url": generate_agreement_url,
            "agreement_required": agreement_required,
            "agreement_template_configured": agreement_template_configured,
            "agreement_configuration_error": (
                "Brak szablonu umowy dla tego formularza."
                if agreement_required and not agreement_template_configured
                else ""
            ),
            "agreement_generated": agreement_template_configured
            and str(row.get("agreement_generated", "")).strip().lower() == "tak",
            "agreement_filename": row.get("agreement_filename", ""),
            "agreement_generated_at": row.get("agreement_generated_at", ""),
            "agreement_generated_at_iso": row.get("agreement_generated_at", "") or today_iso,
            "agreement_signature_valid": str(row.get("agreement_signature_valid", "")).strip().lower() == "tak",
            "agreement_signature_error": row.get("agreement_signature_error", ""),
            "agreement_url": (
                download_url_builder(str(row.get("agreement_filename") or ""), False)
                if (
                    agreement_template_configured
                    and public_status["can_download_agreement"]
                    and is_available(str(row.get("agreement_filename") or ""))
                )
                else ""
            ),
            "agreement_upload_url": (
                agreement_upload_url
                if agreement_template_configured and public_status["can_upload_signed_agreement"]
                else None
            ),
            "training_agreements": training_agreement_views,
            "agreement_items": training_agreement_views,
            "uploadable_training_agreements": [item for item in training_agreement_views if item["can_upload"]],
        }


def _parse_json_list(value: str | list | None) -> list[dict]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    raw = str(value or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]
