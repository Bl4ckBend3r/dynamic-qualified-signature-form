from services.workflow_service import WorkflowService
from services.submission_decision_service import SubmissionDecisionService
from services.submission_workflow_history_service import SubmissionWorkflowHistoryService


class FakeRepository:
    def __init__(self):
        self.rows = {"abc": {"submission_id": "abc", "form_slug": "test", "workflow_step": "submission"}}
        self.events = []
        self.decisions = []

    def get_by_id(self, submission_id):
        return self.rows.get(submission_id)

    def update(self, submission_id, updates):
        self.rows[submission_id].update(updates)
        return True

    def record_workflow_event(self, submission_id, event):
        self.events.append((submission_id, event))
        return True

    def list_workflow_events(self, submission_id):
        return [
            {
                "public_submission_id": submission_id,
                "new_status": "OFFICER_ACCEPTED",
                "new_step": "officer_review",
                "actor_role": "officer",
                "reason": "OK",
                "source": "test",
            }
        ] if self.events else []

    def get_latest_submission_decision(self, submission_id):
        return self.decisions[-1] if self.decisions else None

    def list_submission_decisions(self, submission_id):
        return list(self.decisions)


class FakeSubmission:
    submission_id = "abc"
    form_slug = "test"

    def __init__(self):
        self.process_status = "SUBMITTED"
        self.workflow_step = "submission"


def workflow_config():
    return {
        "workflow": {
            "initial_step": "submission",
            "steps": [
                {"id": "submission", "type": "form_submit", "next": "officer_review"},
                {
                    "id": "officer_review",
                    "type": "manual_decision",
                    "decisions": {"accepted": "declaration", "rejected": "end_rejected"},
                },
                {"id": "declaration", "type": "generate_document", "next": "completed"},
                {"id": "end_rejected", "type": "end"},
                {"id": "completed", "type": "end"},
            ],
        }
    }


def test_resolves_next_step_with_decision():
    service = WorkflowService()

    assert service.resolve_next_step(workflow_config(), "officer_review", "accepted") == "declaration"


def test_builder_officer_stage_requires_explicit_catalog_decision():
    repository = FakeRepository()
    service = WorkflowService(repository)
    config = workflow_config()
    config["workflow"]["steps"][1] = {
        "id": "officer_review", "stage_type": "officer_action", "next": "declaration",
    }
    config["workflow"]["decision_types"] = [{
        "step_id": "officer_review", "code": "conditionally_accepted",
        "label": "Akceptacja warunkowa", "target_step": "declaration",
    }]
    row = repository.rows["abc"]
    service.enter_submission_review(row, config)
    assert row["workflow_step"] == "officer_review"
    assert service.resolve_next_step(config, "officer_review") is None
    assert not service.can_execute_step(row, config, "declaration")
    assert service.resolve_next_step(config, "officer_review", "conditionally_accepted") == "declaration"


def test_transition_updates_repository():
    repository = FakeRepository()
    service = WorkflowService(repository)

    assert service.transition_to("abc", "officer_review")
    assert repository.rows["abc"]["workflow_step"] == "officer_review"
    assert repository.events[0][1]["new_step"] == "officer_review"
    assert repository.events[0][1]["actor_role"] == "system"


def test_transition_submission_validates_when_strict():
    service = WorkflowService()
    submission = FakeSubmission()

    service.transition_submission(submission, "WAITING_FOR_REVIEW", target_step="officer_review", strict=True)

    assert submission.process_status == "WAITING_FOR_REVIEW"
    assert submission.workflow_step == "officer_review"


def test_request_correction_sets_fields():
    repository = FakeRepository()
    service = WorkflowService(repository)

    assert service.request_correction("abc", "Popraw PESEL", ["pesel"])
    assert repository.rows["abc"]["process_status"] == "WAITING_FOR_CORRECTION"
    assert repository.rows["abc"]["correction_required"] == "Tak"
    assert repository.events[0][1]["new_status"] == "WAITING_FOR_CORRECTION"


def test_transition_submission_records_workflow_event_without_actor():
    repository = FakeRepository()
    service = WorkflowService(repository)
    submission = FakeSubmission()

    service.transition_submission(submission, "WAITING_FOR_REVIEW", target_step="officer_review", actor="")

    assert repository.events[0][0] == "abc"
    assert repository.events[0][1]["actor_role"] == "system"
    assert repository.events[0][1]["previous_status"] == "SUBMITTED"


def test_workflow_history_prefers_events_and_falls_back_without_writing(caplog):
    repository = FakeRepository()
    service = SubmissionWorkflowHistoryService(repository)

    fallback = service.list_history({"submission_id": "abc", "process_status": "FORM_SUBMITTED", "workflow_step": "submission"})
    repository.events.append(("abc", {}))
    from_events = service.list_history({"submission_id": "abc", "process_status": "FORM_SUBMITTED", "workflow_step": "submission"})

    assert fallback["source"] == "legacy_form_submission"
    assert fallback["used_legacy_fallback"] is True
    assert from_events["source"] == "submission_workflow_events"
    assert from_events["events"][0]["new_status_label"]


def test_workflow_history_strict_mode_returns_diagnostic_without_fallback(caplog):
    repository = FakeRepository()
    service = SubmissionWorkflowHistoryService(repository, strict_workflow_history_read=True)

    with caplog.at_level("ERROR"):
        result = service.list_history({"submission_id": "abc", "process_status": "FORM_SUBMITTED", "workflow_step": "submission"})

    assert result["source"] == "strict_missing_workflow_events"
    assert result["not_ready"] is True
    assert result["used_legacy_fallback"] is False
    assert result["events"] == []
    assert "strict_workflow_events_missing" in caplog.text
    assert "90010112346" not in caplog.text


def test_decision_service_prefers_submission_decision_and_falls_back_without_mail():
    repository = FakeRepository()
    service = SubmissionDecisionService(repository)
    legacy = service.latest_decision(
        {
            "submission_id": "abc",
            "officer_decision": "accepted",
            "process_status": "OFFICER_ACCEPTED",
            "decision_email_sent": "Tak",
        }
    )
    repository.decisions.append({"decision": "rejected", "target_status": "OFFICER_REJECTED", "email_sent": False})
    current = service.latest_decision({"submission_id": "abc", "officer_decision": "accepted"})

    assert legacy["source"] == "legacy_form_submission"
    assert legacy["decision"]["email_sent"] is True
    assert current["source"] == "submission_decisions"
    assert current["decision"]["decision"] == "rejected"


def test_decision_service_strict_mode_returns_diagnostic_without_fallback(caplog):
    repository = FakeRepository()
    service = SubmissionDecisionService(repository, strict_decision_audit_read=True)

    with caplog.at_level("ERROR"):
        result = service.latest_decision(
            {
                "submission_id": "abc",
                "officer_decision": "accepted",
                "process_status": "OFFICER_ACCEPTED",
                "decision_email_sent": "Tak",
            }
        )

    assert result["source"] == "strict_missing_submission_decision"
    assert result["not_ready"] is True
    assert result["used_legacy_fallback"] is False
    assert result["decision"] is None
    assert "strict_submission_decision_missing" in caplog.text
    assert "90010112346" not in caplog.text


def test_workflow_and_decision_rollback_when_strict_disabled():
    repository = FakeRepository()

    workflow = SubmissionWorkflowHistoryService(repository, strict_workflow_history_read=False).list_history(
        {"submission_id": "abc", "process_status": "FORM_SUBMITTED", "workflow_step": "submission"}
    )
    decision = SubmissionDecisionService(repository, strict_decision_audit_read=False).latest_decision(
        {"submission_id": "abc", "officer_decision": "accepted", "decision_email_sent": "Tak"}
    )

    assert workflow["source"] == "legacy_form_submission"
    assert workflow["used_legacy_fallback"] is True
    assert decision["source"] == "legacy_form_submission"
    assert decision["used_legacy_fallback"] is True


def test_transition_runs_registered_side_effects_and_records_results():
    repository = FakeRepository()
    calls = []

    def generate_document(**context):
        calls.append(context["target_step"])
        return {"filename": "agreement.pdf"}

    service = WorkflowService(
        repository,
        side_effect_handlers={"generate_document": generate_document},
    )

    assert service.transition_to(
        "abc",
        "agreement",
        metadata={
            "side_effects": ["generate_document", "send_email"],
            "user_message": "Umowa jest gotowa.",
        },
    )
    assert calls == ["agreement"]
    event = repository.events[-1][1]
    assert event["side_effects"]["generate_document"]["status"] == "completed"
    assert event["side_effects"]["send_email"]["status"] == "skipped"
    assert event["user_message"] == "Umowa jest gotowa."


def test_explicit_submission_without_decision_runs_document_to_final():
    from unittest.mock import Mock
    repository = FakeRepository()
    document = Mock()
    document.generate_document.return_value = {"enabled": True, "filename": "document.pdf"}
    service = WorkflowService(repository, document_service=document)
    config = {"workflow": {"flow_mode": "explicit", "initial_step": "submission", "steps": [
        {"id": "submission", "type": "form_submit", "stage_type": "user_action", "next": "document"},
        {"id": "document", "stage_type": "document", "action": "generate_document", "document_id": "declaration", "next": "completed"},
        {"id": "completed", "stage_type": "final", "final": True},
    ]}}
    service.enter_submission_review(repository.rows["abc"], config)
    assert repository.rows["abc"]["workflow_stage"] == "completed"
    document.generate_document.assert_called_once()


def test_explicit_decision_stops_before_document_and_branches_per_option():
    from test_workflow_v2_validation import explicit_workflow
    workflow = explicit_workflow()
    workflow["decision_types"] = [{"code": code, "step_id": "review", "target_step": target} for code, target in [("a", "step_a"), ("b", "step_b"), ("c", "completed")]]
    service = WorkflowService(FakeRepository())
    row = service.submission_repository.rows["abc"]
    service.enter_submission_review(row, {"workflow": workflow})
    assert row["workflow_stage"] == "review"
    assert service.resolve_next_step({"workflow": workflow}, "review") is None
    assert [service.resolve_next_step({"workflow": workflow}, "review", code) for code in "abc"] == ["step_a", "step_b", "completed"]


def test_explicit_signature_precedes_decision_and_officer_action_is_not_decision():
    repository = FakeRepository()
    service = WorkflowService(repository)
    config = {"workflow": {"flow_mode": "explicit", "steps": [
        {"id": "submission", "type": "form_submit", "stage_type": "user_action", "next": "signature"},
        {"id": "signature", "stage_type": "document", "action": "await_signature", "next": "review"},
        {"id": "review", "stage_type": "decision"},
        {"id": "officer", "stage_type": "officer_action", "requires_officer_action": True, "next": "review"},
    ]}}
    row = repository.rows["abc"]
    service.enter_submission_review(row, config)
    assert row["workflow_stage"] == "signature"
    service.advance_after_action(row, config)
    assert row["workflow_stage"] == "review"
    assert service.resolve_next_step(config, "officer") == "review"
