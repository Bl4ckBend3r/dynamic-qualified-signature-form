import pytest

from repositories.submission_repository import CsvSubmissionRepository


class FakeStorage:
    def __init__(self):
        self.rows = {}

    def append_csv_row(self, slug, row):
        self.rows.setdefault(slug, []).append(dict(row))

    def read_csv_rows(self, slug):
        return list(self.rows.get(slug, []))

    def update_csv_row_by_submission_id(self, slug, submission_id, updates):
        for row in self.rows.get(slug, []):
            if row["submission_id"] == submission_id:
                row.update(updates)
                return True
        return False


def test_csv_submission_repository_crud():
    storage = FakeStorage()
    repository = CsvSubmissionRepository(storage)

    repository.create({"submission_id": "abc", "form_slug": "test", "email": "a@example.com"})

    assert repository.get_by_id("abc")["email"] == "a@example.com"
    assert repository.update("abc", {"email": "b@example.com"})
    assert repository.list_by_form("test")[0]["email"] == "b@example.com"


def test_postgres_repository_records_p4_dual_write_metadata(tmp_path):
    pytest.importorskip("sqlalchemy")

    from database import create_session_factory
    from models import Base, FormSubmission, SubmissionDecision, SubmissionFile, SubmissionWorkflowEvent
    from repositories.submission_repository import PostgresSubmissionRepository
    from sqlalchemy import create_engine

    database_url = f"sqlite:///{tmp_path / 'repo.db'}"
    engine = create_engine(database_url, future=True)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(database_url)
    repository = PostgresSubmissionRepository(database_url, session_factory=session_factory)

    repository.create({"submission_id": "abc", "form_slug": "sample", "form_name": "Sample"})
    assert repository.record_file(
        "abc",
        {
            "filename": "deklaracja.pdf",
            "storage_path": "output/sample/deklaracje/deklaracja.pdf",
            "document_id": "declaration",
            "document_type": "declaration",
            "status": "generated",
            "signed": False,
            "signature_validation_result": {},
        },
    )
    assert repository.record_file(
        "abc",
        {
            "filename": "deklaracja-signed.pdf",
            "storage_path": "output/sample/deklaracje/deklaracja-signed.pdf",
            "document_id": "declaration",
            "document_type": "signed_declaration",
            "status": "signed",
            "signed": True,
            "signature_status": "valid",
            "signature_validation_result": {"is_signed": True},
        },
    )
    assert repository.record_workflow_event(
        "abc",
        {
            "previous_status": "FORM_SUBMITTED",
            "new_status": "OFFICER_ACCEPTED",
            "previous_step": "submission",
            "new_step": "officer_review",
            "actor_role": "system",
            "reason": "test",
            "source": "pytest",
        },
    )
    assert repository.record_decision(
        "abc",
        {
            "decision": "accepted",
            "justification": "OK",
            "previous_status": "FORM_SUBMITTED",
            "target_status": "OFFICER_ACCEPTED",
            "email_requested": True,
        },
    )

    with session_factory() as db:
        submission = db.query(FormSubmission).filter_by(submission_id="abc").one()
        files = db.query(SubmissionFile).filter_by(submission_id=submission.id).order_by(SubmissionFile.filename).all()
        assert len(files) == 2
        assert files[0].storage_path.startswith("output/sample/")
        signed_file = next(file for file in files if file.signed)
        assert signed_file.signature_validation_result == {"is_signed": True}
        assert db.query(SubmissionWorkflowEvent).filter_by(public_submission_id="abc").count() == 1
        assert db.query(SubmissionDecision).filter_by(public_submission_id="abc").one().decision == "accepted"
