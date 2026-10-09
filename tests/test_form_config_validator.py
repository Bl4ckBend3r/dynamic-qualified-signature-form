import pytest

from services.form_config_service import FormConfigService
from validators.form_config_validator import FormConfigValidator


def test_valid_form_config_passes():
    raw = {
        "title": "Form",
        "fields": [{"type": "text", "name": "first_name"}],
        "documents": [
            {
                "id": "declaration",
                "kind": "generated_pdf",
                "template": "documents_to_sign.html",
                "filename_pattern": "{first_name}_{last_name}.pdf",
            }
        ],
        "workflow": {
            "initial_step": "submission",
            "steps": [
                {"id": "submission", "type": "form_submit", "next": "declaration"},
                {"id": "declaration", "type": "generate_document", "document_id": "declaration"},
            ],
        },
    }
    config = FormConfigService().normalize_form_config(raw)

    assert FormConfigValidator().validate(config) == []


def test_validator_reports_bad_document_and_workflow_refs():
    raw = {
        "title": "Form",
        "fields": [{"type": "text", "name": "first_name"}],
        "documents": [{"id": "", "kind": "generated_pdf", "filename_pattern": "{bad}.pdf"}],
        "workflow": {
            "steps": [
                {"id": "submission", "type": "form_submit", "next": "missing"},
                {"id": "generate", "type": "generate_document", "document_id": "declaration_old"},
            ]
        },
    }
    config = FormConfigService().normalize_form_config(raw)

    errors = FormConfigValidator().validate(config)

    assert "documents[0].id is required" in errors
    assert "documents[0].template is required for generated_pdf" in errors
    assert "workflow.steps[0].next references unknown step: missing" in errors
    assert "workflow.steps[1].document_id references unknown document: declaration_old" in errors
    assert "documents[0].filename_pattern contains unsupported placeholder: bad" in errors


def test_validator_reports_unknown_trigger_and_missing_required_html():
    config = FormConfigService().normalize_form_config(
        {
            "title": "Form",
            "fields": [{"type": "text", "name": "first_name"}],
            "workflow": {
                "initial_step": "submission",
                "requires_declaration": True,
                "declaration_template_source": "html",
                "steps": [{"id": "submission", "type": "end", "triggers": ["unknown_trigger"]}],
            },
        }
    )

    errors = FormConfigValidator(skip_template_check=True).validate(config)

    assert "workflow.steps[0].triggers contains unsupported trigger: unknown_trigger" in errors
    assert "workflow.declaration_template_html is required when declaration is required" in errors


@pytest.mark.parametrize("document_type", ["contract", "declaration"])
@pytest.mark.parametrize("source", ["builder", "docx", "html"])
def test_required_document_accepts_template_from_selected_source(document_type, source):
    workflow = {
        f"requires_{document_type}": True,
        f"{document_type}_template_source": source,
    }
    if source == "builder":
        workflow[f"{document_type}_builder_document"] = {
            "blocks": [{"type": "paragraph", "runs": [{"text": "Treść"}]}]
        }
    elif source == "docx":
        workflow[f"{document_type}_docx_template"] = {
            "original_filename": "szablon.docx",
            "storage_path": f"templates/{document_type}/szablon.docx",
        }
    else:
        workflow[f"{document_type}_template_html"] = "<p>Treść</p>"

    errors = FormConfigValidator(skip_template_check=True).validate(
        {"title": "Form", "fields": [], "documents": [], "workflow": workflow}
    )

    assert errors == []


def test_workflow_normalization_adds_html_document_templates():
    config = FormConfigService().normalize_form_config(
        {
            "title": "Form",
            "fields": [{"type": "text", "name": "first_name"}],
            "workflow": {
                "initial_step": "submission",
                "requires_contract": True,
                "contract_template_html": "<p>Umowa {{ first_name }}</p>",
                "steps": [{"id": "submission", "type": "end", "triggers": ["application_submitted"]}],
            },
        }
    )

    agreement = next(document for document in config["documents"] if document["id"] == "agreement")

    assert agreement["template_html"] == "<p>Umowa {{ first_name }}</p>"
    assert FormConfigValidator(skip_template_check=True).validate(config) == []


def test_admin_managed_contract_disables_legacy_training_agreement_config():
    config = FormConfigService().normalize_form_config(
        {
            "title": "Form",
            "fields": [],
            "documents": [
                {
                    "id": "training_agreement",
                    "kind": "generated_pdf",
                    "enabled": True,
                    "template": "Template/legacy-umowa.html",
                }
            ],
            "workflow": {
                "managed_documents": True,
                "requires_contract": True,
                "contract_template_html": "<main>Admin agreement</main>",
                "initial_step": "submission",
                "steps": [{"id": "submission", "type": "end"}],
            },
        }
    )

    by_id = {document["id"]: document for document in config["documents"]}
    assert by_id["agreement"]["template_html"] == "<main>Admin agreement</main>"
    assert by_id["training_agreement"]["enabled"] is False


def test_field_stage_defaults_and_validates_values():
    config = FormConfigService().normalize_form_config(
        {
            "title": "Form",
            "fields": [
                {"type": "text", "name": "first_name"},
                {"type": "text", "id": "training_selection", "stage": "after_officer_acceptance"},
            ],
        }
    )

    assert config["fields"][0]["stage"] == "initial_submission"
    assert config["fields"][1]["name"] == "training_selection"
    assert config["fields"][1]["stage"] == "after_officer_acceptance"
    assert FormConfigValidator(skip_template_check=True).validate(config) == []


def test_validator_reports_bad_field_stage():
    errors = FormConfigValidator(skip_template_check=True).validate(
        {
            "title": "Form",
            "fields": [{"type": "text", "name": "first_name", "stage": "bad_stage"}],
            "workflow": {"initial_step": "submission", "steps": [{"id": "submission"}]},
        }
    )

    assert "fields[0].stage is unsupported: bad_stage" in errors


def _workflow_reachability_form(*, confirmation: bool, signature_next: str) -> dict:
    return {
        "title": "Formularz szkoleniowy",
        "fields": [],
        "documents": [],
        "workflow": {
            "initial_step": "submission",
            "requires_contract": True,
            "requires_agreement_confirmation": confirmation,
            "contract_template_html": "<p>Umowa</p>",
            "steps": [
                {"id": "submission", "label": "Wniosek", "next": "training_agreements_signature"},
                {
                    "id": "training_agreements_signature",
                    "label": "Umowa oczekuje na podpis beneficjenta",
                    "status": "AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE",
                    "next": signature_next,
                },
                {
                    "id": "stage_10",
                    "label": "Oczekuje na podpis urzędu",
                    "status": "AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE",
                    "next": "stage_11",
                },
                {
                    "id": "stage_11",
                    "label": "Umowa podpisana przez urząd",
                    "status": "AGREEMENT_SIGNED_BY_OFFICE",
                    "next": "completed",
                },
                {"id": "completed", "label": "Zakończenie", "final": True},
            ],
        },
    }


def test_validator_reports_polish_unreachable_labels_and_confirmation_mismatch():
    errors = FormConfigValidator(skip_template_check=True).validate(
        _workflow_reachability_form(confirmation=True, signature_next="completed")
    )

    assert (
        "Etap „Oczekuje na podpis urzędu” nie jest połączony z główną ścieżką workflow."
        in errors
    )
    assert (
        "Etap „Umowa podpisana przez urząd” nie jest połączony z główną ścieżką workflow."
        in errors
    )
    assert any(
        error.startswith("Włączono potwierdzenie podpisania umowy przez urząd")
        for error in errors
    )
    assert not any("workflow contains unreachable step" in error for error in errors)


def test_validator_ignores_unreachable_office_steps_when_confirmation_is_disabled():
    errors = FormConfigValidator(skip_template_check=True).validate(
        _workflow_reachability_form(confirmation=False, signature_next="completed")
    )

    assert not any("Oczekuje na podpis urzędu" in error for error in errors)
    assert not any("Umowa podpisana przez urząd" in error for error in errors)
    assert not any("unreachable" in error for error in errors)
