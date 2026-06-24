from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Callable, Mapping

from services.process_service import ProcessStatus
from services.status_catalog import build_status_view
from services.submission_document_service import SubmissionDocumentType


logger = logging.getLogger(__name__)


class DocumentViewService:
    def build_documents_view(
        self,
        *,
        row: Mapping[str, Any],
        documents_config: list[dict],
        download_url_builder: Callable[[str, bool], str],
        available_actions: list[dict] | None = None,
        document_files: list[Mapping[str, Any]] | None = None,
    ) -> dict:
        documents = []
        files_by_type = self._files_by_type(document_files or [])
        for document in documents_config:
            document_id = str(document.get("id") or "")
            metadata = self._metadata_for_document(files_by_type, document_id, signed=False)
            signed_metadata = self._metadata_for_document(files_by_type, document_id, signed=True)
            used_legacy_fallback = metadata is None
            filename = str((metadata or {}).get("filename") or "").strip()
            if not filename:
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
        status_labeler: Callable[[str, dict], str],
    ) -> dict:
        row = submission["row"]
        status_view = build_status_view(process_state.status.value)
        action_targets = {action.get("target_step") for action in available_actions}
        training_agreements = _parse_json_list(row.get("training_agreements"))
        selected_trainings = _parse_json_list(row.get("selected_trainings"))
        today_iso = date.today().isoformat()
        declaration_enabled = bool(declaration.get("enabled"))
        declaration_filename = declaration.get("filename", "")
        declaration_ready = bool(declaration_enabled and declaration_filename)

        return {
            "submission_id": submission_id,
            "form_slug": submission["form_slug"],
            "form_title": submission["form_title"],
            "message": (
                "Deklaracja zostala wygenerowana i jest gotowa do podpisania."
                if declaration_enabled and declaration.get("created")
                else "Deklaracja jest gotowa do pobrania i podpisania."
                if declaration_ready
                else "Wypelnij deklaracje, aby wygenerowac PDF do podpisu."
                if declaration_enabled
                else "Dla tego formularza deklaracja nie jest wymagana."
            ),
            "process_status": process_state.status.value,
            "current_status": status_view["current_status"],
            "process_status_label": status_labeler(process_state.status.value, form_config),
            "is_final": status_view["is_final"],
            "is_rejected": status_view["is_rejected"],
            "requires_user_action": status_view["requires_user_action"],
            "requires_officer_action": status_view["requires_officer_action"],
            "can_upload": bool(process_state.can_sign_documents and not status_view["is_final"] and not status_view["is_rejected"]),
            "can_download": bool(process_state.can_sign_documents and not status_view["is_rejected"]),
            "visible_steps": [
                step
                for step, visible in {
                    "declaration": declaration_enabled,
                    "agreement": process_state.can_generate_agreement or bool(training_agreements),
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
            "agreement_blocked": str(row.get("agreement_blocked", "")).strip().lower() == "tak",
            "agreement_block_reason": row.get("agreement_block_reason", ""),
            "can_generate_agreement": (
                "agreement" in action_targets
                or "training_agreements" in action_targets
                or process_state.can_generate_agreement
                or (
                    str(row.get("declaration_signature_valid", "")).strip().lower() == "tak"
                    and str(row.get("agreement_generated", "")).strip().lower() != "tak"
                    and str(row.get("agreement_blocked", "")).strip().lower() != "tak"
                )
            ) and bool(selected_trainings),
            "generate_agreement_url": generate_agreement_url,
            "agreement_generated": str(row.get("agreement_generated", "")).strip().lower() == "tak",
            "agreement_filename": row.get("agreement_filename", ""),
            "agreement_generated_at": row.get("agreement_generated_at", ""),
            "agreement_generated_at_iso": row.get("agreement_generated_at", "") or today_iso,
            "agreement_signature_valid": str(row.get("agreement_signature_valid", "")).strip().lower() == "tak",
            "agreement_signature_error": row.get("agreement_signature_error", ""),
            "training_agreements": [
                {
                    **agreement,
                    "url": download_url_builder(agreement.get("filename", ""), False) if agreement.get("filename") else "",
                    "upload_url": agreement_upload_url_builder(agreement.get("id", "")),
                }
                for agreement in training_agreements
            ],
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
