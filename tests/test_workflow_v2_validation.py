from services.workflow_config_service import WorkflowConfigNormalizer, WorkflowConfigValidator
from pathlib import Path


def _step(step_id, *, next_step="", final=False, decisions=None):
    return {
        "id": step_id,
        "admin_label": step_id,
        "user_label": step_id,
        "status": step_id.upper(),
        "next": next_step,
        "final": final,
        "decisions": decisions or {},
    }


def test_v2_graph_reports_dead_end_and_missing_final():
    errors = WorkflowConfigValidator().validate(
        {"schema_version": 2, "initial_step": "start", "steps": [_step("start")]}
    )

    assert any("nie ma wyjścia" in error for error in errors)
    assert any("etapu końcowego" in error for error in errors)


def test_v2_graph_accepts_decision_branches_to_terminal_steps():
    errors = WorkflowConfigValidator().validate(
        {
            "schema_version": 2,
            "initial_step": "review",
            "steps": [
                _step("review", decisions={"yes": "completed", "no": "rejected"}),
                _step("completed", final=True),
                {**_step("rejected", final=True), "rejected": True},
            ],
        }
    )

    assert errors == []


def test_v2_graph_reports_unreachable_stage():
    errors = WorkflowConfigValidator().validate(
        {
            "schema_version": 2,
            "initial_step": "start",
            "steps": [
                _step("start", next_step="completed"),
                _step("orphan", final=True),
                _step("completed", final=True),
            ],
        }
    )

    assert any("nieosiągalny" in error for error in errors)


def test_admin_editor_contains_live_svg_decision_graph():
    template = Path("templates/admin/forms/edit.html").read_text(encoding="utf-8")

    assert "data-workflow-diagram" in template
    assert "function renderWorkflowDiagram" in template
    assert 'make("polygon"' in template
    assert "data-step-stage-type" in template
    assert 'name="decision_{{ decision_id }}_correction_status"' in template
    assert 'name="decision_{{ decision_id }}_user_message"' in template
    assert "buildWorkflowDiagramDecisions" in template
    assert "Scalono techniczne przejścia etapu z konkretną decyzją" in template
    assert 'label: "Decyzja"' not in template


def test_admin_editor_contains_centered_layered_interactive_diagram():
    template = Path("templates/admin/forms/edit.html").read_text(encoding="utf-8")
    stylesheet = Path("static/css/admin.css").read_text(encoding="utf-8")

    assert "data-workflow-diagram-viewport" in template
    assert "data-workflow-diagram-fit" in template
    assert "data-workflow-diagram-zoom-in" in template
    assert "data-workflow-diagram-layout-reset" in template
    assert "workflowDiagramState.positions" in template
    assert "startWorkflowNodeDrag" in template
    assert "diagram_layout" in template
    assert "Układ automatyczny" in template
    assert "Układ ręczny" in template
    assert "justify-content: center" in stylesheet
    assert "max-height: min(55vh, 520px)" in stylesheet
    assert "overflow: auto" in stylesheet


def test_layered_diagram_uses_orthogonal_edges_and_side_columns():
    template = Path("templates/admin/forms/edit.html").read_text(encoding="utf-8")

    assert "const columns = {correction:" in template
    assert "main: 420" in template
    assert "decision: 820" in template
    assert "rejection: 1200" in template
    assert "pathData = `M ${start.x} ${start.y} L" in template
    assert "const ranks = new Map()" in template
    assert "const rankBuckets = new Map()" in template
    assert "verticalBlocked" in template


def test_diagram_validation_can_focus_invalid_stage():
    template = Path("templates/admin/forms/edit.html").read_text(encoding="utf-8")

    assert "Element diagramu z błędem" in template
    assert "focusWorkflowNode" in template
    assert "WorkflowStageEditor.validate" in template
    assert "message?.focus()" in template


def test_inactive_decision_without_assignment_or_transition_does_not_block_workflow():
    errors = WorkflowConfigValidator().validate(
        {
            "schema_version": 2,
            "initial_step": "start",
            "steps": [_step("start", next_step="completed"), _step("completed", final=True)],
            "decision_settings": [
                {
                    "id": "draft",
                    "label": "Szkic decyzji",
                    "active": False,
                    "step_id": "",
                    "yes_status": "",
                    "no_status": "",
                }
            ],
        }
    )

    assert errors == []


def test_active_decision_requires_assignment_and_real_transition():
    base = {
        "schema_version": 2,
        "initial_step": "start",
        "steps": [_step("start", next_step="completed"), _step("completed", final=True)],
    }
    missing_assignment = WorkflowConfigValidator().validate(
        {
            **base,
            "decision_settings": [
                {"id": "custom", "label": "Aktywna", "active": True, "yes_status": "COMPLETED"}
            ],
        }
    )
    missing_transition = WorkflowConfigValidator().validate(
        {
            **base,
            "decision_settings": [
                {"id": "custom", "label": "Aktywna", "active": True, "step_id": "start"}
            ],
        }
    )

    assert any("nie ma przypisanego etapu" in error for error in missing_assignment)
    assert any("przynajmniej jedno realne przejście" in error for error in missing_transition)


def test_active_decision_reports_nonexistent_assigned_stage():
    errors = WorkflowConfigValidator().validate(
        {
            "schema_version": 2,
            "initial_step": "start",
            "steps": [_step("start", next_step="completed"), _step("completed", final=True)],
            "decision_settings": [
                {
                    "id": "custom",
                    "label": "Błędna decyzja",
                    "active": True,
                    "step_id": "missing_stage",
                    "yes_status": "COMPLETED",
                }
            ],
        }
    )

    assert any("wskazuje nieistniejący etap „missing_stage”" in error for error in errors)


def test_admin_editor_collapses_inactive_decisions_and_adds_from_stage():
    template = Path("templates/admin/forms/edit.html").read_text(encoding="utf-8")

    assert "<details class=\"workflow-inactive-decisions\"" in template
    assert "Zaawansowane: decyzje nieaktywne i nieprzypisane" in template
    assert "Te decyzje nie są obecnie częścią aktywnego workflow." in template
    assert "workflow_stage_editor.js" in template
    assert "data-workflow-decision-template" in template
    assert "decision_settings: allDecisionSettings()" in template


def test_workflow_normalizer_preserves_valid_manual_layout():
    workflow = {
        "initial_step": "start",
        "steps": [_step("start", next_step="completed"), _step("completed", final=True)],
        "diagram_layout": {
            "nodes": {
                "start": {"x": 120, "y": 40},
                "decision:start:0": {"x": 420.25, "y": 180.5},
                "invalid": {"x": "not-a-number", "y": 20},
            }
        },
    }

    normalized = WorkflowConfigNormalizer().normalize(workflow)

    assert normalized["diagram_layout"] == {
        "nodes": {
            "start": {"x": 120.0, "y": 40.0},
            "decision:start:0": {"x": 420.25, "y": 180.5},
        }
    }
    assert "diagram_layout" not in WorkflowConfigNormalizer().advanced_elements(workflow)


def test_workflow_without_diagram_layout_remains_automatic():
    normalized = WorkflowConfigNormalizer().normalize(
        {
            "initial_step": "start",
            "steps": [_step("start", next_step="completed"), _step("completed", final=True)],
        }
    )

    assert "diagram_layout" not in normalized


def explicit_workflow():
    return {"schema_version": 2, "flow_mode": "explicit", "name": "Proces", "initial_step": "submission",
            "steps": [{**_step("submission", next_step="review"), "type": "form_submit", "stage_type": "user_action"},
                      {**_step("review"), "stage_type": "decision", "decision_name": "Rozpatrzenie", "decision_scope": "submission"},
                      {**_step("completed", final=True), "stage_type": "final"}],
            "decision_types": [{"code": code, "label": code, "semantic_category": "neutral", "scope": "submission", "step_id": "review", "target_step": "completed", "require_reason": False} for code in ("a", "b")]}


def test_explicit_capabilities_do_not_repair_or_hide_graph():
    workflow = explicit_workflow()
    workflow.update(requires_contract=True, requires_declaration=True, requires_agreement_confirmation=True)
    normalized = WorkflowConfigNormalizer().normalize(workflow)
    assert [s["id"] for s in normalized["steps"]] == [s["id"] for s in workflow["steps"]]
    assert all(s["active"] for s in normalized["steps"])
    assert WorkflowConfigValidator().validate(workflow) == []


def test_explicit_validator_checks_final_unknown_reference_and_minimum_options():
    from copy import deepcopy
    base = explicit_workflow()
    assert WorkflowConfigValidator().validate(base) == []
    for mutate, expected in [
        (lambda w: w["steps"][-1].update(next="submission"), "końcowy"),
        (lambda w: w["decision_types"][0].update(target_step="unknown"), "nieistniejący"),
        (lambda w: w.update(decision_types=w["decision_types"][:1]), "2 opcji"),
        (lambda w: w["steps"][0].update(id=""), "identyfikatorem"),
        (lambda w: w.update(initial_step=""), "początkowy"),
        (lambda w: w["steps"][0].update(transitions=None), "tablicą JSON"),
        (lambda w: w["steps"][0].update(next="submission"), "sam na siebie"),
    ]:
        workflow = deepcopy(base)
        mutate(workflow)
        assert any(expected in e for e in WorkflowConfigValidator().validate(workflow))


def test_explicit_correction_cycle_with_exit_and_three_branches_is_valid():
    workflow = explicit_workflow()
    workflow["steps"].insert(2, {**_step("correction", next_step="review"), "stage_type": "user_action"})
    workflow["decision_types"][0]["target_step"] = "correction"
    workflow["decision_types"].append({**workflow["decision_types"][1], "code": "c"})
    assert WorkflowConfigValidator().validate(workflow) == []
    workflow["steps"][2]["next"] = "correction"
    workflow["steps"][2]["allow_loop"] = True
    assert any("bez zakończenia" in e for e in WorkflowConfigValidator().validate(workflow))


def test_explicit_validator_rejects_same_catalog_id_with_different_codes():
    workflow = explicit_workflow()
    workflow['decision_types'][0]['definition_id'] = 17
    workflow['decision_types'][1]['definition_id'] = '17'
    assert any('DecisionOption ID' in error for error in WorkflowConfigValidator().validate(workflow))
    workflow['decision_types'][1]['definition_id'] = 18
    assert WorkflowConfigValidator().validate(workflow) == []


def test_full_form_validator_checks_explicit_catalog_branches_and_final():
    from validators.form_config_validator import FormConfigValidator
    config = {"title": "Test", "fields": [], "workflow": explicit_workflow(), "documents": []}
    validator = FormConfigValidator()
    assert validator.validate(config) == []
    config["workflow"]["steps"][-1]["next"] = "submission"
    assert any("końcowy" in error for error in validator.validate(config))
