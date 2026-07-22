from services.workflow_config_service import (
    WorkflowConfigNormalizer,
    WorkflowConfigValidator,
    repair_agreement_confirmation_path,
    workflow_status_label,
    workflow_status_options,
)


def _training_agreement_workflow(*, confirmation: bool, signature_next: str = "completed"):
    return {
        "name": "Szkolenia",
        "initial_step": "submission",
        "requires_contract": True,
        "requires_agreement_confirmation": confirmation,
        "steps": [
            {"id": "submission", "status": "FORM_SUBMITTED", "next": "training_agreements"},
            {"id": "training_agreements", "status": "AGREEMENT_GENERATED", "next": "training_agreements_signature"},
            {
                "id": "training_agreements_signature",
                "admin_label": "Umowa oczekuje na podpis beneficjenta",
                "status": "AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE",
                "next": signature_next,
            },
            {
                "id": "stage_10",
                "admin_label": "Oczekuje na podpis urzędu",
                "status": "AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE",
                "next": "stage_11",
            },
            {
                "id": "stage_11",
                "admin_label": "Umowa podpisana przez urząd",
                "status": "AGREEMENT_SIGNED_BY_OFFICE",
                "next": "completed",
            },
            {"id": "completed", "status": "PROCESS_COMPLETED", "final": True},
        ],
    }


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

    assert any("włączoną obsługę umów" in error and "aktywnego etapu umowy" in error for error in errors)
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

    base["requires_contract"] = True
    errors = WorkflowConfigValidator().validate(base)

    assert any(
        "Potwierdzenie umowy" in error
        and "Weryfikacja" in error
        and "Obsługa umów jest włączona" in error
        for error in errors
    )


def test_validator_ignores_stale_agreement_decisions_when_agreement_is_not_required():
    errors = WorkflowConfigValidator().validate(
        {
            "initial_step": "review",
            "requires_contract": False,
            "requires_agreement_confirmation": True,
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
                    "label": "Historyczne potwierdzenie umowy",
                    "step_id": "removed_agreement_stage",
                }
            ],
        }
    )

    assert errors == []


def test_validator_ignores_agreement_decisions_when_office_confirmation_is_disabled():
    errors = WorkflowConfigValidator().validate(
        {
            "initial_step": "agreement",
            "requires_contract": True,
            "requires_agreement_confirmation": False,
            "steps": [
                {
                    "id": "agreement",
                    "admin_label": "Umowa do podpisania",
                    "user_label": "Podpisz umowę",
                    "status": "AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE",
                }
            ],
            "decision_settings": [
                {
                    "id": "agreement_confirmation",
                    "label": "Historyczne potwierdzenie umowy",
                    "step_id": "removed_agreement_stage",
                }
            ],
        }
    )

    assert errors == []


def test_validator_ignores_decision_assigned_to_an_inactive_declaration_step():
    errors = WorkflowConfigValidator().validate(
        {
            "initial_step": "submission",
            "requires_declaration": False,
            "steps": [
                {
                    "id": "submission",
                    "admin_label": "Wniosek",
                    "user_label": "Wniosek",
                    "status": "FORM_SUBMITTED",
                },
                {
                    "id": "declaration_signature",
                    "admin_label": "Podpis deklaracji",
                    "user_label": "Podpis deklaracji",
                    "status": "DECLARATION_UPLOADED",
                },
            ],
            "decision_settings": [
                {
                    "id": "declaration_confirmation",
                    "label": "Historyczne potwierdzenie deklaracji",
                    "step_id": "declaration_signature",
                }
            ],
        }
    )

    assert errors == []


def test_legacy_agreement_status_does_not_satisfy_current_agreement_validation():
    errors = WorkflowConfigValidator().validate(
        {
            "initial_step": "legacy",
            "requires_contract": True,
            "requires_agreement_confirmation": True,
            "steps": [
                {
                    "id": "legacy",
                    "admin_label": "Stary etap umowy",
                    "user_label": "Stary etap umowy",
                    "status": "AGREEMENT_UPLOADED",
                }
            ],
        }
    )

    assert any("aktywnego etapu umowy" in error for error in errors)
    assert any("Potwierdzenie podpisania umowy przez urząd" in error for error in errors)


def test_current_agreement_confirmation_stage_passes_validation():
    errors = WorkflowConfigValidator().validate(
        {
            "initial_step": "office_signature",
            "requires_contract": True,
            "requires_agreement_confirmation": True,
            "steps": [
                {
                    "id": "office_signature",
                    "admin_label": "Potwierdzenie podpisania umowy przez urząd",
                    "user_label": "Umowa oczekuje na urząd",
                    "status": "AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE",
                }
            ],
            "decision_settings": [
                {
                    "id": "agreement_confirmation",
                    "label": "Potwierdzenie podpisania umowy przez urząd",
                    "step_id": "office_signature",
                }
            ],
        }
    )

    assert errors == []


def test_normalizer_repairs_known_office_confirmation_shortcut():
    normalized = WorkflowConfigNormalizer().normalize(
        _training_agreement_workflow(confirmation=True)
    )
    by_id = {step["id"]: step for step in normalized["steps"]}

    assert by_id["training_agreements_signature"]["next"] == "stage_10"
    assert by_id["stage_10"]["next"] == "stage_11"
    assert by_id["stage_11"]["next"] == "completed"
    assert by_id["stage_10"]["active"] is True
    assert by_id["stage_11"]["active"] is True
    assert WorkflowConfigValidator().validate(normalized) == []


def test_office_confirmation_steps_are_inactive_when_confirmation_is_disabled():
    normalized = WorkflowConfigNormalizer().normalize(
        _training_agreement_workflow(confirmation=False, signature_next="stage_10")
    )
    by_id = {step["id"]: step for step in normalized["steps"]}

    assert by_id["training_agreements_signature"]["next"] == "completed"
    assert by_id["stage_10"]["active"] is False
    assert by_id["stage_11"]["active"] is False
    assert WorkflowConfigValidator().validate(normalized) == []


def test_repair_does_not_overwrite_a_custom_agreement_confirmation_path():
    workflow = _training_agreement_workflow(
        confirmation=True,
        signature_next="custom_office_review",
    )
    workflow["steps"].insert(
        -1,
        {
            "id": "custom_office_review",
            "status": "AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE",
            "next": "completed",
        },
    )

    repaired, changed = repair_agreement_confirmation_path(workflow)

    by_id = {step["id"]: step for step in repaired["steps"]}
    assert changed is False
    assert by_id["training_agreements_signature"]["next"] == "custom_office_review"
