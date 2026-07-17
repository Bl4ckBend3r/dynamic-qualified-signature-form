from services.workflow_config_service import (
    WorkflowConfigNormalizer,
    WorkflowConfigValidator,
    workflow_status_label,
    workflow_status_options,
)


def test_legacy_statuses_have_readable_labels():
    assert workflow_status_label("application_submitted") == "Wniosek złożony"
    assert workflow_status_label("officer_accepted") == "Wniosek zaakceptowany"
    assert workflow_status_label("officer_rejected") == "Wniosek odrzucony"
    assert workflow_status_label("document_generated") == "Dokument wygenerowany"
    assert workflow_status_label("document_uploaded") == "Dokument wgrany"
    assert workflow_status_label("contract_required") == "Umowa wymagana"
    assert workflow_status_label("declaration_required") == "Deklaracja wymagana"


def test_new_workflow_options_use_office_signature_statuses_and_modernize_old_label():
    options = {item["value"] for item in workflow_status_options()}
    normalized = WorkflowConfigNormalizer().normalize(
        {
            "initial_step": "office_signature",
            "steps": [
                {
                    "id": "office_signature",
                    "status": "AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE",
                    "label": "Umowa podpisana przez beneficjenta",
                }
            ],
        }
    )

    assert "AGREEMENT_SIGNED_BY_OFFICE" in options
    assert "BENEFICIARY_AGREEMENT_CONFIRMED" not in options
    assert normalized["steps"][0]["admin_label"] == "Umowa podpisana przez urząd"
    assert normalized["steps"][0]["user_label"] == "Umowa podpisana przez urząd"


def test_normalizer_preserves_advanced_workflow_and_step_fields():
    source = {
        "name": "Proces",
        "initial_step": "submission",
        "custom_engine_option": {"retry": 3},
        "steps": [
            {
                "id": "submission",
                "status": "application_submitted",
                "custom_step_option": "keep-me",
            }
        ],
    }

    normalizer = WorkflowConfigNormalizer()
    result = normalizer.normalize(source)

    assert result["custom_engine_option"] == {"retry": 3}
    assert result["steps"][0]["custom_step_option"] == "keep-me"
    assert result["steps"][0]["admin_label"] == "Wniosek złożony"
    assert "custom_engine_option" in normalizer.advanced_elements(result)
    assert "submission: custom_step_option" in normalizer.advanced_elements(result)


def test_validator_requires_initial_stage_and_existing_transitions():
    errors = WorkflowConfigValidator().validate(
        {
            "initial_step": "missing",
            "steps": [
                {
                    "id": "submission",
                    "admin_label": "Wniosek",
                    "user_label": "Wniosek",
                    "status": "FORM_SUBMITTED",
                    "next": "unknown",
                }
            ],
        }
    )

    assert "Status początkowy wskazuje nieistniejący etap." in errors
    assert any("prowadzi do nieistniejącego etapu" in error for error in errors)


def test_validator_requires_agreement_and_confirmation_stages():
    errors = WorkflowConfigValidator().validate(
        {
            "initial_step": "submission",
            "requires_contract": True,
            "requires_agreement_confirmation": True,
            "steps": [
                {
                    "id": "submission",
                    "admin_label": "Wniosek",
                    "user_label": "Wniosek",
                    "status": "FORM_SUBMITTED",
                }
            ],
        }
    )

    assert "Proces wymaga umowy, ale nie ma etapu umowy." in errors
    assert any("Potwierdzenie podpisania umowy przez urząd" in error for error in errors)


def test_officer_agreement_decision_is_allowed_only_after_upload():
    base = {
        "initial_step": "review",
        "steps": [
            {
                "id": "review",
                "admin_label": "Weryfikacja",
                "user_label": "Weryfikacja",
                "status": "OFFICER_REVIEW",
            }
        ],
        "decision_settings": [
            {
                "id": "agreement_confirmation",
                "label": "Potwierdzenie umowy",
                "step_id": "review",
            }
        ],
    }

    errors = WorkflowConfigValidator().validate(base)

    assert any("nie może być dostępna na wybranym etapie" in error for error in errors)
