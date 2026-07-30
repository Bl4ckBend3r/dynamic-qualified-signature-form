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
    assert "min-height: 440px" in stylesheet
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
    assert "aktywna decyzja musi mieć przynajmniej jedno realne przejście" in template
    assert "brakuje etapu podpisu urzędu" in template


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
    assert "Dodaj decyzję do tego etapu" in template
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
