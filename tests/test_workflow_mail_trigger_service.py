from types import SimpleNamespace

from services.workflow_mail_trigger_service import WorkflowMailTriggerService


def test_trigger_catalog_uses_only_active_workflow_steps_events_and_decisions():
    form = SimpleNamespace(
        definition_json={
            "workflow": {
                "name": "Obsługa wniosku",
                "initial_step": "submission",
                "steps": [
                    {
                        "id": "submission",
                        "admin_label": "Złożenie wniosku",
                        "status": "FORM_SUBMITTED",
                        "triggers": ["application_submitted"],
                    },
                    {
                        "id": "review",
                        "admin_label": "Ocena urzędnika",
                        "status": "OFFICER_REVIEW",
                        "requires_officer_action": True,
                        "decisions": {
                            "accepted": "completed",
                            "rejected": "rejected",
                        },
                    },
                    {
                        "id": "inactive",
                        "admin_label": "Wyłączony etap",
                        "status": "INACTIVE_STATUS",
                        "active": False,
                        "triggers": ["inactive_event"],
                        "decisions": {"inactive_decision": "completed"},
                    },
                ],
                "decision_settings": [
                    {
                        "id": "application_decision",
                        "label": "Decyzja o wniosku",
                        "step_id": "review",
                        "active": True,
                        "outcomes": [
                            {"code": "yes", "label": "Akceptacja"},
                            {"code": "no", "label": "Odrzucenie"},
                        ],
                    },
                    {
                        "id": "disabled_decision",
                        "label": "Decyzja wyłączona",
                        "step_id": "review",
                        "active": False,
                    },
                    {
                        "id": "orphan_decision",
                        "label": "Decyzja bez etapu",
                        "step_id": "removed",
                        "active": True,
                    },
                ],
            }
        }
    )

    catalog = WorkflowMailTriggerService().options_for_form(form)

    assert catalog["has_workflow"] is True
    assert [item["value"] for item in catalog["statuses"]] == ["FORM_SUBMITTED", "OFFICER_REVIEW"]
    assert [item["value"] for item in catalog["events"]] == ["correction_accepted", "application_submitted"]
    assert [item["value"] for item in catalog["decisions"]] == ["accepted", "rejected"]
    assert all(item["source"] == "workflow" for item in catalog["statuses"])
    assert all(item["stage_id"] in {"submission", "review"} for item in catalog["statuses"])


def test_trigger_catalog_without_workflow_uses_safe_system_events_only():
    catalog = WorkflowMailTriggerService().options_for_form(SimpleNamespace(definition_json={"fields": []}))

    assert catalog["has_workflow"] is False
    assert catalog["statuses"] == []
    assert catalog["decisions"] == []
    assert [item["value"] for item in catalog["events"]] == [
        "manual",
        "manual_bulk",
        "application_submitted",
        "submission_received",
    ]
    assert all(item["source"] == "system" for item in catalog["events"])
    assert "nie ma aktywnego workflow" in catalog["message"]
