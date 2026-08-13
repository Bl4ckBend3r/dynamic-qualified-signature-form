from __future__ import annotations

from copy import deepcopy

from form_loader import extract_submission_data, form_definition_for_stage, validate_submission
from services.documents.declaration_flow_service import DeclarationFlowService
from services.field_availability_service import FieldAvailabilityService
from validators.form_config_validator import FormConfigValidator


def workflow_definition():
    return {
        "title": "Availability",
        "workflow": {
            "initial_step": "submission",
            "steps": [
                {"id": "submission", "admin_label": "Złożenie", "requires_user_action": True, "next": "officer_review"},
                {"id": "officer_review", "admin_label": "Weryfikacja", "requires_officer_action": True, "next": "declaration"},
                {"id": "declaration", "admin_label": "Deklaracja", "requires_user_action": True, "next": "agreement"},
                {"id": "agreement", "admin_label": "Umowa", "requires_user_action": True, "next": "completed"},
                {"id": "completed", "admin_label": "Koniec", "final": True},
            ],
        },
        "fields": [],
    }


def field(name, availability, **extra):
    return {"name": name, "label": name, "type": "text", "availability": availability, **extra}


def test_fields_can_be_initial_later_or_on_multiple_steps():
    definition = workflow_definition()
    definition["fields"] = [
        field("initial", [{"step": "submission", "visible": True, "editable": True, "required": True}]),
        field("later", [{"step": "declaration", "visible": True, "editable": True, "required": True}]),
        field("multi", [
            {"step": "declaration", "visible": True, "editable": True, "required": False},
            {"step": "agreement", "visible": True, "editable": False, "required": False},
        ]),
    ]
    assert [item["name"] for item in FieldAvailabilityService().editable_fields(definition, "submission")] == ["initial"]
    assert [item["name"] for item in FieldAvailabilityService().editable_fields(definition, "declaration")] == ["later", "multi"]
    agreement = form_definition_for_stage(definition, "agreement")["fields"]
    assert agreement[0]["name"] == "multi" and agreement[0]["readonly"] is True


def test_hidden_and_readonly_values_are_not_accepted_or_required():
    definition = workflow_definition()
    definition["fields"] = [
        field("hidden", [{"step": "declaration", "visible": False, "editable": False, "required": False}]),
        field("readonly", [{"step": "declaration", "visible": True, "editable": False, "required": False}]),
        field("required", [{"step": "declaration", "visible": True, "editable": True, "required": True}]),
    ]
    step_definition = form_definition_for_stage(definition, "declaration")
    data = extract_submission_data(step_definition, {"hidden": "attack", "readonly": "attack", "required": ""})
    assert "hidden" not in data and "readonly" not in data
    assert validate_submission(step_definition, data) == {"required": "Pole „required” jest wymagane."}


def test_step_update_merges_only_editable_values_and_keeps_other_data():
    definition = workflow_definition()
    definition["fields"] = [
        field("earlier", [{"step": "submission", "visible": True, "editable": True, "required": True}]),
        field("account", [{"step": "declaration", "visible": True, "editable": True, "required": True}]),
        field("readonly", [{"step": "declaration", "visible": True, "editable": False, "required": False}]),
    ]

    class Repository:
        def __init__(self):
            self.updates = []
            self.events = []

        def update(self, submission_id, updates):
            self.updates.append((submission_id, updates))

        def record_workflow_event(self, submission_id, event):
            self.events.append((submission_id, event))
            return True

    repository = Repository()
    submission = {"row": {"data_json": {"earlier": "keep", "readonly": "old"}, "workflow_stage": "declaration"}}
    result = DeclarationFlowService().save_additional_fields(
        submission_id="sub-1", submission=submission, form_config=definition,
        form_data={"account": "PL00", "readonly": "attack", "earlier": "erase"},
        submission_repository=repository,
    )
    assert result.success
    updates = repository.updates[0][1]
    assert updates["data_json"] == {"earlier": "keep", "readonly": "old", "account": "PL00"}
    assert "readonly" not in {key for key in updates if key != "data_json"}
    assert repository.events[0][1]["side_effects"] == {"changed_fields": ["account"]}
    assert "PL00" not in str(repository.events[0][1])


def test_required_if_and_file_are_scoped_to_the_active_step():
    definition = workflow_definition()
    definition["fields"] = [
        field("choice", [{"step": "submission", "visible": True, "editable": True, "required": False}]),
        field(
            "details",
            [{"step": "declaration", "visible": True, "editable": True, "required": False}],
            required_if={"field": "choice", "operator": "equals", "value": "yes"},
        ),
        {
            "name": "proof", "label": "Proof", "type": "file", "required_if": {"field": "choice", "operator": "equals", "value": "yes"},
            "availability": [{"step": "declaration", "visible": True, "editable": True, "required": False}],
        },
    ]
    assert all(item.get("name") != "proof" for item in form_definition_for_stage(definition, "submission")["fields"])
    assert any(item.get("name") == "proof" for item in form_definition_for_stage(definition, "declaration")["fields"])
    declaration = form_definition_for_stage(definition, "declaration")
    assert validate_submission(declaration, {"choice": "yes"})["details"] == "Pole „details” jest wymagane."
    assert validate_submission(form_definition_for_stage(definition, "submission"), {"choice": "yes"}) == {}


def test_dangling_step_blocks_validation_and_legacy_stages_are_mapped():
    definition = workflow_definition()
    definition["fields"] = [field("account", [{"step": "removed", "visible": True, "editable": True, "required": False}])]
    errors = FieldAvailabilityService().validate_config(definition)
    assert errors == ['Pole "account" odwołuje się do nieistniejącego etapu "removed".']

    legacy = workflow_definition()
    legacy["fields"] = [
        {"name": "first", "type": "text", "stage": "initial_submission", "required": True},
        {"name": "later", "type": "text", "stage": "after_officer_acceptance", "required": True},
    ]
    service = FieldAvailabilityService()
    assert service.normalize_field(legacy["fields"][0], legacy)["availability"][0]["step"] == "submission"
    assert service.normalize_field(legacy["fields"][1], legacy)["availability"][0]["step"] == "declaration"


def test_version_snapshot_availability_is_independent():
    original = workflow_definition()
    original["fields"] = [field("account", [{"step": "declaration", "visible": True, "editable": True, "required": True}])]
    archived_snapshot = deepcopy(original)
    original["fields"][0]["availability"][0]["step"] = "agreement"
    assert archived_snapshot["fields"][0]["availability"][0]["step"] == "declaration"
