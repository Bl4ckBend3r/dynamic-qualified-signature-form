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
                "steps": [{"id": "submission", "type": "end", "triggers": ["unknown_trigger"]}],
            },
        }
    )

    errors = FormConfigValidator(skip_template_check=True).validate(config)

    assert "workflow.steps[0].triggers contains unsupported trigger: unknown_trigger" in errors
    assert "workflow.declaration_template_html is required when declaration is required" in errors


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
