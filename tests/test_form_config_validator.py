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
