from services.workflow_config_service import WorkflowConfigValidator
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
