from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from models import Base, Form, FormPermission, FormSubmission, SubmissionAssignmentHistory, User
from services.submission_assignment_service import (
    SubmissionAssignmentPermissionError,
    SubmissionAssignmentService,
)


@pytest.fixture()
def assignment_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'assignments.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        actor = User(email="manager@example.test", password_hash="x", role="admin")
        anna = User(email="anna@example.test", password_hash="x", role="form_manager")
        jan = User(email="jan@example.test", password_hash="x", role="form_manager")
        ola = User(email="ola@example.test", password_hash="x", role="form_manager")
        disabled = User(email="disabled@example.test", password_hash="x", role="form_manager", is_active=False)
        reviewer = User(email="reviewer@example.test", password_hash="x", role="form_manager")
        no_access = User(email="no-access@example.test", password_hash="x", role="form_manager")
        form = Form(slug="case-form", name="Case form", definition_json={})
        db.add_all([actor, anna, jan, ola, disabled, reviewer, no_access, form])
        db.flush()
        db.add_all([
            FormPermission(user_id=actor.id, form_id=form.id, can_manage=False, can_review=True, can_assign_submissions=True),
            FormPermission(user_id=anna.id, form_id=form.id, can_manage=False, can_assign_submissions=True),
            FormPermission(user_id=jan.id, form_id=form.id, can_manage=False, can_assign_submissions=True),
            FormPermission(user_id=ola.id, form_id=form.id, can_manage=False, can_assign_submissions=True),
            FormPermission(user_id=disabled.id, form_id=form.id, can_manage=False, can_assign_submissions=True),
            FormPermission(user_id=reviewer.id, form_id=form.id, can_manage=False, can_review=True),
        ])
        submissions = [
            FormSubmission(submission_id=f"case-{index}", form_slug=form.slug, form_name=form.name)
            for index in range(1, 7)
        ]
        db.add_all(submissions)
        db.commit()
        ids = {
            "actor": actor.id, "anna": anna.id, "jan": jan.id, "ola": ola.id,
            "disabled": disabled.id, "reviewer": reviewer.id, "no_access": no_access.id, "form": form.id,
            "submissions": [item.id for item in submissions],
        }
    yield factory, ids
    engine.dispose()


def test_manual_reassignment_unassignment_metadata_and_immutable_history(assignment_db):
    factory, ids = assignment_db
    service = SubmissionAssignmentService()
    with factory() as db:
        form = db.get(Form, ids["form"])
        actor = db.get(User, ids["actor"])
        submission = db.get(FormSubmission, ids["submissions"][0])
        service.assign(db, submission, form, assignee_id=ids["anna"], actor=actor, reason="start")
        service.assign(db, submission, form, assignee_id=ids["jan"], actor=actor, reason="replacement")
        service.update_case_metadata(
            submission, priority="urgent", due_at=datetime.now(timezone.utc) + timedelta(days=1)
        )
        service.assign(db, submission, form, assignee_id=None, actor=actor, reason="queue")
        db.commit()

        history = db.execute(
            select(SubmissionAssignmentHistory).order_by(SubmissionAssignmentHistory.id)
        ).scalars().all()
        assert [(item.previous_user_id, item.assigned_to_user_id) for item in history] == [
            (None, ids["anna"]), (ids["anna"], ids["jan"]), (ids["jan"], None)
        ]
        assert submission.assigned_to_user_id is None
        assert submission.priority == "urgent"
        assert submission.due_at is not None
        history[0].reason = "tampered"
        with pytest.raises(ValueError, match="immutable"):
            db.flush()


def test_assignment_requires_permission_and_eligible_target(assignment_db):
    factory, ids = assignment_db
    service = SubmissionAssignmentService()
    with factory() as db:
        form = db.get(Form, ids["form"])
        actor = db.get(User, ids["reviewer"])
        submission = db.get(FormSubmission, ids["submissions"][0])
        with pytest.raises(SubmissionAssignmentPermissionError):
            service.assign(db, submission, form, assignee_id=ids["jan"], actor=actor)


def test_combined_assignment_filters_and_sql_queue_counts(assignment_db):
    factory, ids = assignment_db
    service = SubmissionAssignmentService()
    now = datetime.now(timezone.utc)
    with factory() as db:
        form = db.get(Form, ids["form"])
        actor = db.get(User, ids["actor"])
        first = db.get(FormSubmission, ids["submissions"][0])
        second = db.get(FormSubmission, ids["submissions"][1])
        service.assign(db, first, form, assignee_id=ids["anna"], actor=actor)
        service.update_case_metadata(first, priority="high", due_at=now - timedelta(hours=1))
        second.process_status = "AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE"
        db.commit()

        query = service.apply_filters(
            select(FormSubmission), {"assignee": str(ids["anna"]), "priority": "high", "overdue": "1"},
            current_user_id=ids["anna"],
        )
        assert db.execute(query).scalars().all() == [first]
        counts = service.queue_counts(db, user_id=ids["anna"], form_slugs=[form.slug])
        assert counts["mine"] == 1
        assert counts["unassigned"] == 5
        assert counts["overdue"] == 1
        assert counts["waiting_office_signature"] == 1


def test_round_robin_rotates_three_users_and_skips_ineligible_accounts(assignment_db):
    factory, ids = assignment_db
    service = SubmissionAssignmentService()
    configured = [ids["anna"], ids["disabled"], ids["reviewer"], ids["jan"], ids["no_access"], ids["ola"]]
    assigned = [
        service.auto_assign_created(
            factory,
            submission_id=f"case-{index}",
            form_config={"assignment": {"mode": "round_robin", "eligible_users": configured}},
        )
        for index in range(1, 5)
    ]
    assert assigned == [ids["anna"], ids["jan"], ids["ola"], ids["anna"]]
    with factory() as db:
        sources = db.execute(select(SubmissionAssignmentHistory.source)).scalars().all()
        assert sources == ["round_robin"] * 4
