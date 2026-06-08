from services.workflow_service import WorkflowService


class FakeRepository:
    def __init__(self):
        self.rows = {"abc": {"submission_id": "abc", "form_slug": "test", "workflow_step": "submission"}}

    def get_by_id(self, submission_id):
        return self.rows.get(submission_id)

    def update(self, submission_id, updates):
        self.rows[submission_id].update(updates)
        return True


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


def test_transition_updates_repository():
    repository = FakeRepository()
    service = WorkflowService(repository)

    assert service.transition_to("abc", "officer_review")
    assert repository.rows["abc"]["workflow_step"] == "officer_review"


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
