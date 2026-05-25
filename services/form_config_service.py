from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


class FormConfigService:
    def normalize_form_config(self, raw_config: dict) -> dict:
        config = deepcopy(raw_config or {})
        config["documents"] = self.normalize_documents_config(config)
        return self.build_default_workflow_if_missing(config)

    def normalize_documents_config(self, raw_config: dict) -> list[dict]:
        raw_documents = raw_config.get("documents")
        if isinstance(raw_documents, list):
            return [self._normalize_document(document) for document in raw_documents if isinstance(document, Mapping)]

        if isinstance(raw_documents, Mapping):
            return [
                self._normalize_document({"id": document_id, **dict(document or {})})
                for document_id, document in raw_documents.items()
            ]

        process = raw_config.get("process") or {}
        process_documents = process.get("documents") if isinstance(process, Mapping) else None
        if isinstance(process_documents, Mapping):
            return [
                self._normalize_document({"id": document_id, **dict(document or {})})
                for document_id, document in process_documents.items()
            ]

        return []

    def build_default_workflow_if_missing(self, form_config: dict) -> dict:
        if isinstance(form_config.get("workflow"), Mapping):
            return form_config

        document_ids = {document.get("id") for document in form_config.get("documents", [])}
        steps: list[dict[str, Any]] = [
            {"id": "submission", "type": "form_submit", "next": "officer_review"},
            {
                "id": "officer_review",
                "type": "manual_decision",
                "decisions": {
                    "accepted": (
                        "declaration"
                        if "declaration" in document_ids
                        else "agreement"
                        if "agreement" in document_ids
                        else "training_agreements"
                        if "training_agreement" in document_ids
                        else "completed"
                    ),
                    "rejected": "end_rejected",
                    "correction": "waiting_for_correction",
                },
            },
        ]

        if "declaration" in document_ids:
            steps.extend(
                [
                    {
                        "id": "declaration",
                        "type": "generate_document",
                        "document_id": "declaration",
                        "next": "declaration_signature",
                    },
                    {
                        "id": "declaration_signature",
                        "type": "signature_upload",
                        "document_id": "declaration",
                        "next": (
                            "agreement"
                            if "agreement" in document_ids
                            else "training_agreements"
                            if "training_agreement" in document_ids
                            else "completed"
                        ),
                    },
                ]
            )

        if "agreement" in document_ids:
            steps.extend(
                [
                    {
                        "id": "agreement",
                        "type": "generate_document",
                        "document_id": "agreement",
                        "next": "agreement_signature",
                    },
                    {
                        "id": "agreement_signature",
                        "type": "signature_upload",
                        "document_id": "agreement",
                        "next": "completed",
                    },
                ]
            )

        if "training_agreement" in document_ids:
            steps.extend(
                [
                    {
                        "id": "training_agreements",
                        "type": "generate_documents",
                        "document_id": "training_agreement",
                        "repeat_over": "selected_trainings",
                        "next": "training_agreements_signature",
                    },
                    {
                        "id": "training_agreements_signature",
                        "type": "signature_upload_many",
                        "document_id": "training_agreement",
                        "repeat_over": "selected_trainings",
                        "next": "completed",
                    },
                ]
            )

        steps.extend(
            [
                {"id": "waiting_for_correction", "type": "correction", "next": "officer_review"},
                {"id": "end_rejected", "type": "end"},
                {"id": "completed", "type": "end"},
            ]
        )
        form_config["workflow"] = {"initial_step": "submission", "steps": steps}
        return form_config

    def _normalize_document(self, document: Mapping[str, Any]) -> dict:
        normalized = dict(document)
        document_id = str(normalized.get("id") or "").strip()
        normalized["id"] = document_id
        normalized.setdefault("label", document_id.replace("_", " ").title())
        normalized.setdefault("kind", "generated_pdf")
        normalized.setdefault("signature_required", True)
        normalized.setdefault("allowed_signatures", ["mszafir", "profil_zaufany"])
        if "enabled" in normalized:
            normalized["enabled"] = bool(normalized.get("enabled"))
        else:
            normalized["enabled"] = True
        return normalized
