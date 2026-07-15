from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from services.document_service import DocumentType


@dataclass
class AgreementFlowResult:
    success: bool
    message: str | None = None
    agreements: list[dict[str, Any]] = field(default_factory=list)
    error_code: str | None = None


class AgreementFlowService:
    def form_config_with_training_adapter(self, *, form_config: dict, document_service) -> tuple[dict, dict]:
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

    def generate_training_agreements(
        self,
        *,
        submission: dict,
        form_config: dict,
        document_service,
        generated_date: str | None = None,
    ) -> AgreementFlowResult:
        if submission["row"].get("declaration_signature_valid", "").strip().lower() != "tak":
            return AgreementFlowResult(
                success=False,
                message="Najpierw wgraj poprawnie podpisana deklaracje.",
                error_code="declaration_signature_required",
            )
        resolved_form_config, document = self.form_config_with_training_adapter(
            form_config=form_config,
            document_service=document_service,
        )
        resolved_date = generated_date or date.today().isoformat()
        agreements = document_service.generate_documents_for_collection(
            submission,
            resolved_form_config,
            document["id"],
            document.get("repeat_over") or "selected_trainings",
            document.get("repeat_item_alias") or "training",
            context_extra={"generated_date": resolved_date},
        )
        return AgreementFlowResult(
            success=True,
            message=f"Wygenerowano umowy: {len(agreements)}.",
            agreements=agreements,
        )

    @staticmethod
    def form_config_with_participant_agreement_notification(form_config: dict) -> tuple[dict, str]:
        return form_config, "AGREEMENT_SIGNED"

    def send_participant_agreement_signed_notification(
        self,
        *,
        services,
        slug: str,
        submission_id: str,
        agreement_id: str | None,
        upload_result: dict,
        get_submission_context,
        get_form_config,
    ) -> list[dict]:
        return []
