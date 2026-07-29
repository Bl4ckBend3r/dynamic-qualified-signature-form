import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from models import Base, FormSubmission, SubmissionTraining
from routes.admin.forms import _used_training_ids_for_form


def test_used_training_ids_include_rows_and_legacy_document_history():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        normalized = FormSubmission(
            submission_id="normalized",
            form_slug="sample",
            form_name="Sample",
        )
        legacy = FormSubmission(
            submission_id="legacy",
            form_slug="sample",
            form_name="Sample",
            selected_trainings=json.dumps(
                [{"id": "legacy-selected", "name": "Legacy"}]
            ),
            training_agreements=json.dumps(
                [
                    {"training_id": "generated-agreement"},
                    {"training": {"id": "signed-agreement"}},
                ]
            ),
        )
        other_form = FormSubmission(
            submission_id="other",
            form_slug="other",
            form_name="Other",
            selected_trainings=json.dumps(
                [{"id": "not-in-scope", "name": "Other"}]
            ),
        )
        db.add_all([normalized, legacy, other_form])
        db.flush()
        db.add(
            SubmissionTraining(
                submission_id=normalized.id,
                training_id="normalized-row",
                training_name_snapshot="Normalized",
                training_price_snapshot="100.00",
            )
        )
        db.flush()

        used = _used_training_ids_for_form(db, "sample")

    assert used == {
        "normalized-row",
        "legacy-selected",
        "generated-agreement",
        "signed-agreement",
    }
