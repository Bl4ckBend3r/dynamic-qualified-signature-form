from __future__ import annotations

import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select

from database import create_session_factory
from models import Form, FormSubmission, SubmissionTraining
from services.submission_training_service import SubmissionTrainingService, TrainingSelectionError


DATABASES = [
    ("postgresql", os.getenv("P3_POSTGRES_TEST_URL", "")),
    ("mariadb", os.getenv("P3_MARIADB_TEST_URL", "")),
]


def _database_parameters():
    return [pytest.param(url, id=name, marks=pytest.mark.skipif(not url, reason=f"{name} integration URL is not configured")) for name, url in DATABASES]


def _run_capacity_scenario(database_url: str, *, capacity: int, attempts: int, prelocked: int = 0) -> tuple[int, int]:
    session_factory = create_session_factory(database_url)
    slug = f"p3-concurrency-{uuid.uuid4().hex}"
    training_id = "last-seat-training"
    public_ids = []
    with session_factory() as db:
        db.add(Form(slug=slug, name="Synthetic concurrency form", definition_json={}))
        db.flush()
        for index in range(prelocked + attempts):
            public_id = f"CONC-{uuid.uuid4().hex}"
            agreement_id = f"agreement-{index}"
            agreement = {
                "id": agreement_id,
                "training_id": training_id,
                "training": {"id": training_id, "name": "Synthetic training", "price": "1.00", "capacity": capacity},
            }
            submission = FormSubmission(
                submission_id=public_id,
                form_slug=slug,
                form_name="Synthetic concurrency form",
                data_json={},
                selected_trainings="[]",
                training_agreements=json.dumps([agreement]),
            )
            db.add(submission)
            db.flush()
            locked = index < prelocked
            db.add(
                SubmissionTraining(
                    submission_id=submission.id,
                    training_id=training_id,
                    training_name_snapshot="Synthetic training",
                    training_price_snapshot="1.00",
                    training_snapshot={"id": training_id, "name": "Synthetic training", "price": "1.00", "capacity": capacity},
                    status="agreement_uploaded_by_beneficiary" if locked else "selected",
                    is_locked=locked,
                    agreement_id=agreement_id,
                )
            )
            if not locked:
                public_ids.append((public_id, agreement_id))
        db.commit()

    start = threading.Event()

    def reserve(item: tuple[str, str]) -> bool:
        start.wait(timeout=10)
        with session_factory() as db:
            submission = db.execute(select(FormSubmission).where(FormSubmission.submission_id == item[0])).scalar_one()
            try:
                SubmissionTrainingService().lock_for_agreement(db, submission, item[1])
                db.commit()
                return True
            except TrainingSelectionError:
                db.rollback()
                return False

    with ThreadPoolExecutor(max_workers=min(20, attempts)) as pool:
        futures = [pool.submit(reserve, item) for item in public_ids]
        start.set()
        successes = sum(1 for future in futures if future.result(timeout=60))

    with session_factory() as db:
        locked_count = db.execute(
            select(func.count()).select_from(SubmissionTraining).join(FormSubmission).where(
                FormSubmission.form_slug == slug,
                SubmissionTraining.training_id == training_id,
                SubmissionTraining.is_locked.is_(True),
            )
        ).scalar_one()
        duplicate_count = db.execute(
            select(func.count()).select_from(
                select(SubmissionTraining.submission_id, SubmissionTraining.training_id)
                .join(FormSubmission)
                .where(FormSubmission.form_slug == slug)
                .group_by(SubmissionTraining.submission_id, SubmissionTraining.training_id)
                .having(func.count() > 1)
                .subquery()
            )
        ).scalar_one()
    assert duplicate_count == 0
    return successes, locked_count


@pytest.mark.parametrize("database_url", _database_parameters())
def test_ten_participants_competing_for_one_seat(database_url):
    successes, locked = _run_capacity_scenario(database_url, capacity=1, attempts=10)
    assert successes == 1
    assert locked == 1


@pytest.mark.parametrize("database_url", _database_parameters())
def test_fifty_participants_competing_for_twenty_seats_with_existing_occupancy(database_url):
    successes, locked = _run_capacity_scenario(database_url, capacity=20, attempts=50, prelocked=5)
    assert successes == 15
    assert locked == 20
