from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from services.document_service import DocumentType
from services.process_service import ProcessStatus


@dataclass
class AgreementFlowResult:
    success: bool
    message: str | None = None
    agreements: list[dict[str, Any]] = field(default_factory=list)
    error_code: str | None = None


class AgreementFlowService:
    def form_config_with_training_adapter(self, *, form_config: dict, document_service) -> tuple[dict, dict]:
        training_document = document_service.get_document_by_id(form_config, DocumentType.TRAINING_AGREEMENT)
        agreement_document = document_service.get_document_by_id(form_config, DocumentType.AGREEMENT)
        admin_inline_agreement = bool(agreement_document and str(agreement_document.get("template_html") or "").strip())
        if training_document and training_document.get("enabled", True) and not admin_inline_agreement:
            return form_config, training_document
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
        agreement_document = document_service.get_document_by_id(form_config, DocumentType.AGREEMENT)
        training_document = document_service.get_document_by_id(form_config, DocumentType.TRAINING_AGREEMENT)
        source_document = (
            agreement_document
            if agreement_document and str(agreement_document.get("template_html") or "").strip()
            else training_document or agreement_document
        )
        if not source_document or not source_document.get("enabled", True):
            return AgreementFlowResult(
                success=False,
                message="Umowa nie jest wymagana dla tego formularza.",
                error_code="agreement_not_required",
            )
        if not str(source_document.get("template_html") or source_document.get("template") or "").strip():
            return AgreementFlowResult(
                success=False,
                message="Brak szablonu umowy dla tego formularza.",
                error_code="agreement_template_missing",
            )
        resolved_date = generated_date or date.today().isoformat()
        existing_agreements = _json_list(submission["row"].get("training_agreements"))
        existing_training_ids = {
            str(item.get("training_id") or item.get("id") or "").strip()
            for item in existing_agreements
            if str(item.get("filename") or "").strip()
        }
        selected_trainings = _json_list(submission["row"].get("selected_trainings"))
        pending_trainings = [
            item for item in selected_trainings
            if str(item.get("id") or item.get("training_id") or "").strip() not in existing_training_ids
        ]
        if existing_agreements and not pending_trainings:
            return AgreementFlowResult(
                success=True,
                message="Wszystkie wybrane szkolenia mają już wygenerowane umowy.",
                agreements=existing_agreements,
            )
        generation_submission = submission
        if existing_agreements:
            generation_submission = {
                **submission,
                "row": {
                    **submission["row"],
                    "selected_trainings": json.dumps(pending_trainings, ensure_ascii=False),
                },
            }
        # Training agreements are always generated one file per selected training.
        generation_mode = "per_training"
        uses_explicit_training_document = bool(
            training_document
            and training_document.get("enabled", True)
            and not (agreement_document and str(agreement_document.get("template_html") or "").strip())
        )
        if not uses_explicit_training_document and generation_mode == "single":
            generated = document_service.generate_document(
                submission,
                form_config,
                DocumentType.AGREEMENT,
                context_extra={"generated_date": resolved_date, "agreement_generated_at": resolved_date},
                force=True,
            )
            return AgreementFlowResult(
                success=True,
                message="Wygenerowano umowę.",
                agreements=[generated],
            )

        resolved_form_config, document = self.form_config_with_training_adapter(
            form_config=form_config,
            document_service=document_service,
        )
        generated_agreements = document_service.generate_documents_for_collection(
            generation_submission,
            resolved_form_config,
            document["id"],
            document.get("repeat_over") or "selected_trainings",
            document.get("repeat_item_alias") or "training",
            context_extra={"generated_date": resolved_date},
        )
        agreements = [*existing_agreements, *generated_agreements]
        if existing_agreements:
            updates = {
                "training_agreements": json.dumps(agreements, ensure_ascii=False),
                "agreement_filename": str(agreements[0].get("filename") or ""),
                "agreement_generated": "Tak",
                "process_status": ProcessStatus.AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE.value,
            }
            document_service.submission_repository.update(submission["submission_id"], updates)
            submission["row"].update(updates)
        return AgreementFlowResult(
            success=True,
            message=f"Wygenerowano umowy: {len(generated_agreements)}.",
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


def _json_list(value) -> list[dict]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
