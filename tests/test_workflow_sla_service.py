from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from models import (
    Base,
    BusinessCalendar,
    BusinessCalendarHoliday,
    Form,
    FormSubmission,
    FormVersion,
    SubmissionDeadlineNotification,
    SubmissionStepDeadline,
)
from services.workflow_sla_service import WorkflowSlaService, add_duration


NOW = datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc)  # Friday


def _engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return engine


def _seed(db: Session, *, reminders=None, escalation=None, actor_type="participant"):
    workflow = {
        "initial_step": "correction",
        "steps": [
            {
                "id": "correction",
                "admin_label": "Korekta",
                "requires_user_action": True,
                "sla": {
                    "deadline": {"value": 2, "unit": "business_days"},
                    "actor_type": actor_type,
                    "reminders": reminders or [],
                    "escalation": escalation or {},
                },
            }
        ],
    }
    form = Form(slug="test", name="Test", definition_json={"workflow": workflow})
    version = FormVersion(form=form, version_major=1, version_minor=0, version_label="1.0", definition_json={"workflow": workflow})
    submission = FormSubmission(
        submission_id="public-1", form_slug="test", form_name="Test", form_version=version,
        workflow_step="correction", workflow_stage="correction", email="participant@example.org",
    )
    db.add_all([form, version, submission])
    db.commit()
    return submission, workflow


def test_add_duration_supports_hours_and_calendar_days():
    assert add_duration(NOW, 6, "hours") == NOW + timedelta(hours=6)
    assert add_duration(NOW, 3, "calendar_days") == NOW + timedelta(days=3)


def test_business_days_skip_weekend_and_configured_holiday():
    monday_holiday = date(2026, 8, 17)
    assert add_duration(NOW, 1, "business_days") == datetime(2026, 8, 17, 10, tzinfo=timezone.utc)
    assert add_duration(NOW, 1, "business_days", holidays={monday_holiday}) == datetime(2026, 8, 18, 10, tzinfo=timezone.utc)
    assert add_duration(datetime(2026, 8, 17, 10, tzinfo=timezone.utc), -1, "business_days") == NOW


def test_calendar_day_preserves_local_wall_clock_across_dst():
    warsaw = ZoneInfo("Europe/Warsaw")
    start = datetime(2026, 10, 24, 10, 0, tzinfo=warsaw)
    result = add_duration(start, 1, "calendar_days")
    assert result.hour == 10
    assert result.utcoffset() != start.utcoffset()


def test_enter_step_uses_entered_at_and_freezes_calendar_snapshot():
    engine = _engine()
    with Session(engine) as db:
        submission, workflow = _seed(db)
        calendar = BusinessCalendar(name="Urząd", weekend_days=[5, 6])
        calendar.holidays.append(BusinessCalendarHoliday(holiday_date=date(2026, 8, 17), name="Dzień wolny"))
        db.add(calendar)
        db.commit()
        workflow["steps"][0]["sla"]["business_calendar_id"] = calendar.id
        submission.form_version.definition_json = {"workflow": workflow}
        row = WorkflowSlaService().enter_step(db, submission, "correction", form_config={"workflow": workflow}, now=NOW)
        db.commit()
        assert row.entered_at.replace(tzinfo=timezone.utc) == NOW
        assert row.due_at.replace(tzinfo=timezone.utc) == datetime(2026, 8, 19, 10, tzinfo=timezone.utc)
        assert row.sla_definition_snapshot["deadline"] == {"value": 2, "unit": "business_days"}
        calendar.holidays.clear()
        db.commit()
        assert row.sla_definition_snapshot["holidays"] == ["2026-08-17"]


def test_completion_stops_old_deadline_and_return_creates_new_instance():
    engine = _engine()
    service = WorkflowSlaService()
    with Session(engine) as db:
        submission, workflow = _seed(db)
        first = service.enter_step(db, submission, "correction", form_config={"workflow": workflow}, now=NOW)
        service.transition(db, submission, previous_step="correction", new_step="review", form_config={"workflow": workflow}, now=NOW + timedelta(hours=1))
        assert first.status == "completed"
        assert first.completed_at is not None
        submission.workflow_step = "correction"
        second = service.enter_step(db, submission, "correction", form_config={"workflow": workflow}, now=NOW + timedelta(days=1))
        db.commit()
        assert second.id != first.id
        assert db.scalar(select(SubmissionStepDeadline).where(SubmissionStepDeadline.status == "active")).id == second.id


class _FakeDispatcher:
    def __init__(self):
        self.calls = []

    def select_template(self, templates, submission=None, event_type=None):
        return None

    def dispatch_to_submission(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(status="sent", error_message="", log=None)


def test_before_after_escalation_and_repeated_jobs_are_idempotent():
    engine = _engine()
    dispatcher = _FakeDispatcher()
    service = WorkflowSlaService(dispatcher)
    reminders = [
        {"offset": -1, "unit": "calendar_days", "recipients": ["participant"]},
        {"offset": 1, "unit": "calendar_days", "recipients": ["participant"]},
    ]
    escalation = {"offset": 2, "unit": "business_days", "recipients": ["participant"]}
    with Session(engine) as db:
        submission, workflow = _seed(db, reminders=reminders, escalation=escalation)
        deadline = service.enter_step(db, submission, "correction", form_config={"workflow": workflow}, now=NOW)
        deadline.due_at = NOW
        db.commit()
        first = service.process_due(db, now=NOW + timedelta(days=5))
        second = service.process_due(db, now=NOW + timedelta(days=5))
        assert first["sent"] == 3
        assert second["claimed"] == 0
        assert len(dispatcher.calls) == 3
        assert {call["event_type"] for call in dispatcher.calls} == {"sla_before_deadline", "sla_overdue", "sla_escalation"}
        assert db.query(SubmissionDeadlineNotification).count() == 3


def test_completed_deadline_never_dispatches():
    engine = _engine()
    dispatcher = _FakeDispatcher()
    service = WorkflowSlaService(dispatcher)
    with Session(engine) as db:
        submission, workflow = _seed(db, reminders=[{"offset": 0, "unit": "hours", "recipients": ["participant"]}])
        deadline = service.enter_step(db, submission, "correction", form_config={"workflow": workflow}, now=NOW)
        deadline.due_at = NOW
        deadline.status = "completed"
        deadline.completed_at = NOW
        db.commit()
        result = service.process_due(db, now=NOW + timedelta(hours=1))
        assert result["examined"] == 0
        assert dispatcher.calls == []


def test_two_parallel_jobs_cannot_claim_same_delivery(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'sla.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    dispatcher = _FakeDispatcher()
    dispatcher_lock = Lock()
    original_dispatch = dispatcher.dispatch_to_submission

    def locked_dispatch(**kwargs):
        with dispatcher_lock:
            return original_dispatch(**kwargs)

    dispatcher.dispatch_to_submission = locked_dispatch
    service = WorkflowSlaService(dispatcher)
    with Session(engine) as db:
        submission, workflow = _seed(db, reminders=[{"offset": 0, "unit": "hours", "recipients": ["participant"]}])
        deadline = service.enter_step(db, submission, "correction", form_config={"workflow": workflow}, now=NOW)
        deadline.due_at = NOW
        db.commit()

    def run_job():
        with Session(engine) as db:
            return service.process_due(db, now=NOW + timedelta(minutes=1))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _value: run_job(), range(2)))
    assert sum(item["claimed"] for item in results) == 1
    assert len(dispatcher.calls) == 1
    with Session(engine) as db:
        assert db.query(SubmissionDeadlineNotification).count() == 1
