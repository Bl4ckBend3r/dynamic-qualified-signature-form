import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from models import (
    Base, Form, FormPermission, FormSubmission, FormVersion, SubmissionChecklistItemResult,
    SubmissionChecklistResultHistory,
    SubmissionFile, User, VerificationChecklistDefinition, VerificationChecklistItemDefinition,
)
from services.verification_checklist_service import (
    VerificationChecklistError, VerificationChecklistPermissionError, VerificationChecklistService,
)


@pytest.fixture()
def checklist_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'checklists.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session.begin() as db:
        form = Form(slug="manual-review", name="Manual review", definition_json={})
        officer = User(email="anna@example.test", password_hash="x", role="admin")
        no_review = User(email="viewer@example.test", password_hash="x", role="admin")
        limited = User(email="limited@example.test", password_hash="x", role="admin")
        db.add_all([form, officer, no_review, limited]); db.flush()
        version = FormVersion(
            form_id=form.id, version_major=1, version_minor=0, version_label="1.0", status="draft",
            definition_json={"fields": [{"name": "email"}], "workflow": {"steps": [{"id": "officer_review"}]}},
        )
        db.add(version); db.flush()
        db.add_all([
            FormPermission(user_id=officer.id, form_id=form.id, can_manage=False, can_review=True, can_make_decision=True, can_view_sensitive_data=True),
            FormPermission(user_id=no_review.id, form_id=form.id, can_manage=False, can_review=False, can_make_decision=False, can_view_sensitive_data=False),
            FormPermission(user_id=limited.id, form_id=form.id, can_manage=False, can_review=True, can_make_decision=False, can_view_sensitive_data=False),
        ])
        service = VerificationChecklistService()
        checklist = service.create_checklist(db, version, name="Weryfikacja formalna", workflow_step="officer_review")
        blocking = service.add_item(db, checklist, key="signed", label="Dokument podpisany właściwie", blocking=True, required=True, document_required=True, allow_not_applicable=False)
        optional = service.add_item(db, checklist, key="match", label="Zgodność danych", blocking=False, required=True, document_required=False, allow_not_applicable=True, position=1)
        sensitive = service.add_item(
            db, checklist, key="health", label="Dane zdrowotne",
            sensitive=True, required=False, position=2,
        )
        version.status = "published"
        submission = FormSubmission(submission_id="public-1", form_slug=form.slug, form_name=form.name, form_version_id=version.id, workflow_step="officer_review")
        other = FormSubmission(submission_id="public-2", form_slug=form.slug, form_name=form.name, form_version_id=version.id, workflow_step="officer_review")
        db.add_all([submission, other]); db.flush()
        own_file = SubmissionFile(submission_id=submission.id, public_submission_id=submission.submission_id, form_slug=form.slug, filename="own.pdf", storage_path="x/own.pdf")
        foreign_file = SubmissionFile(submission_id=other.id, public_submission_id=other.submission_id, form_slug=form.slug, filename="foreign.pdf", storage_path="x/foreign.pdf")
        sensitive_file = SubmissionFile(submission_id=submission.id, public_submission_id=submission.submission_id, form_slug=form.slug, filename="health.pdf", storage_path="x/health.pdf", data_classification="sensitive")
        db.add_all([own_file, foreign_file, sensitive_file]); db.flush()
        ids = {name: obj.id for name, obj in {"form": form, "officer": officer, "no_review": no_review, "limited": limited, "version": version, "submission": submission, "other": other, "blocking": blocking, "optional": optional, "sensitive": sensitive, "own_file": own_file, "foreign_file": foreign_file, "sensitive_file": sensitive_file}.items()}
    yield Session, ids
    engine.dispose()


def test_yes_no_na_comment_evidence_history_and_decision_validation(checklist_db):
    Session, ids = checklist_db
    service = VerificationChecklistService()
    with Session.begin() as db:
        submission = db.get(FormSubmission, ids["submission"]); officer = db.get(User, ids["officer"])
        blocking = db.get(VerificationChecklistItemDefinition, ids["blocking"])
        optional = db.get(VerificationChecklistItemDefinition, ids["optional"])
        initial = service.validate_for_decision(db, submission)
        assert not initial.valid and any("Nie oceniono" in error for error in initial.errors)
        result = service.save_result(db, submission, blocking, result="no", comment="Brak podpisu", evidence_file_ids=[ids["own_file"]], officer=officer)
        assert result.comment == "Brak podpisu" and result.evidence_links[0].submission_file_id == ids["own_file"]
        blocked = service.validate_for_decision(db, submission)
        assert any("wynik NIE" in error for error in blocked.errors)
        service.save_result(db, submission, blocking, result="yes", comment="Podpis sprawdzony", evidence_file_ids=[ids["own_file"]], officer=officer)
        service.save_result(db, submission, optional, result="not_applicable", comment="N/D", evidence_file_ids=[], officer=officer)
        assert service.validate_for_decision(db, submission).valid
        history = db.execute(select(SubmissionChecklistResultHistory)).scalars().all()
        assert [(row.previous_result, row.new_result) for row in history] == [("no", "yes")]


def test_na_forbidden_foreign_evidence_and_permission_are_rejected(checklist_db):
    Session, ids = checklist_db
    service = VerificationChecklistService()
    with Session.begin() as db:
        submission = db.get(FormSubmission, ids["submission"])
        item = db.get(VerificationChecklistItemDefinition, ids["blocking"])
        officer = db.get(User, ids["officer"]); viewer = db.get(User, ids["no_review"])
        with pytest.raises(VerificationChecklistError, match="Nie dotyczy"):
            service.save_result(db, submission, item, result="not_applicable", comment="", evidence_file_ids=[], officer=officer)
        with pytest.raises(VerificationChecklistError, match="tego zgłoszenia"):
            service.save_result(db, submission, item, result="yes", comment="", evidence_file_ids=[ids["foreign_file"]], officer=officer)
        with pytest.raises(VerificationChecklistPermissionError):
            service.save_result(db, submission, item, result="yes", comment="", evidence_file_ids=[], officer=viewer)


def test_clone_preserves_historical_definition(checklist_db):
    Session, ids = checklist_db
    service = VerificationChecklistService()
    with Session.begin() as db:
        source = db.get(FormVersion, ids["version"])
        target = FormVersion(form_id=source.form_id, version_major=1, version_minor=1, version_label="1.1", status="draft", definition_json=source.definition_json)
        db.add(target); db.flush()
        service.clone_definitions(db, source.id, target)
        clone = service.list_for_version(db, target.id)[0]
        clone.items[0].label = "Nowa treść"
        assert service.list_for_version(db, source.id)[0].items[0].label == "Dokument podpisany właściwie"


def test_sensitive_item_and_evidence_require_sensitive_permission(checklist_db):
    Session, ids = checklist_db
    service = VerificationChecklistService()
    with Session.begin() as db:
        submission = db.get(FormSubmission, ids["submission"])
        limited = db.get(User, ids["limited"])
        normal_item = db.get(VerificationChecklistItemDefinition, ids["blocking"])
        sensitive_item = db.get(VerificationChecklistItemDefinition, ids["sensitive"])
        with pytest.raises(VerificationChecklistPermissionError, match="chronionego dokumentu"):
            service.save_result(
                db, submission, normal_item, result="yes", comment="",
                evidence_file_ids=[ids["sensitive_file"]], officer=limited,
            )
        with pytest.raises(VerificationChecklistPermissionError, match="wrażliwego kryterium"):
            service.save_result(
                db, submission, sensitive_item, result="yes", comment="",
                evidence_file_ids=[], officer=limited,
            )


def test_published_definition_and_history_are_immutable(checklist_db):
    Session, ids = checklist_db
    service = VerificationChecklistService()
    with Session() as db:
        item = db.get(VerificationChecklistItemDefinition, ids["blocking"])
        item.label = "Niedozwolona zmiana"
        with pytest.raises(ValueError, match="immutable"):
            db.flush()
        db.rollback()
        submission = db.get(FormSubmission, ids["submission"]); officer = db.get(User, ids["officer"])
        item = db.get(VerificationChecklistItemDefinition, ids["blocking"])
        service.save_result(db, submission, item, result="no", comment="a", evidence_file_ids=[], officer=officer)
        service.save_result(db, submission, item, result="yes", comment="b", evidence_file_ids=[], officer=officer)
        db.flush()
        history = db.execute(select(SubmissionChecklistResultHistory)).scalar_one()
        history.new_result = "no"
        with pytest.raises(ValueError, match="immutable"):
            db.flush()


def test_blocking_no_does_not_block_negative_decision(checklist_db):
    Session, ids = checklist_db
    service = VerificationChecklistService()
    with Session.begin() as db:
        submission = db.get(FormSubmission, ids["submission"]); officer = db.get(User, ids["officer"])
        item = db.get(VerificationChecklistItemDefinition, ids["blocking"])
        service.save_result(db, submission, item, result="no", comment="", evidence_file_ids=[], officer=officer)
        assert not service.validate_for_decision(db, submission).valid
        assert service.validate_for_decision(db, submission, decision="rejected").valid
        assert service.validate_for_decision(db, submission, decision="correction").valid


def test_delete_draft_definitions_refuses_to_destroy_historical_results(checklist_db):
    Session, ids = checklist_db
    service = VerificationChecklistService()
    with Session.begin() as db:
        source = db.get(FormVersion, ids["version"])
        draft = FormVersion(
            form_id=source.form_id,
            version_major=2,
            version_minor=0,
            version_label="2.0",
            status="draft",
            definition_json=source.definition_json,
        )
        db.add(draft)
        db.flush()
        checklist = service.create_checklist(
            db, draft, name="Historyczna", workflow_step="officer_review"
        )
        item = service.add_item(db, checklist, key="history", label="Historyczne kryterium")
        db.add(
            SubmissionChecklistItemResult(
                submission_id=ids["submission"],
                checklist_item_definition_id=item.id,
                result="yes",
            )
        )
        db.flush()

        with pytest.raises(VerificationChecklistError, match="historyczne wyniki"):
            service.delete_item(db, item)
        with pytest.raises(VerificationChecklistError, match="historyczne wyniki"):
            service.delete_checklist(db, checklist)

        assert db.get(VerificationChecklistItemDefinition, item.id) is item
        assert db.get(VerificationChecklistDefinition, checklist.id) is checklist
