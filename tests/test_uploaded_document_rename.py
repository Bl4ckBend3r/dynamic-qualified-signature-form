import json
import importlib
import unicodedata
from datetime import datetime, timezone
from hashlib import sha256
from types import SimpleNamespace

from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.schema import CreateTable
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest

from database import create_session_factory
from models import Base, FormSubmission, SubmissionFile, SubmissionWorkflowEvent, UploadedDocumentRename
from scripts.rename_uploaded_documents import RenameError, UploadedDocumentRenamer, _document_id, _nested_references
from scripts import rename_uploaded_documents as rename_script
from services.documents.document_storage_service import DocumentStorageService
from services.nextcloud_storage import NextcloudStorage
from testing_support import InMemoryStorage


def _environment(tmp_path):
    session_factory = create_session_factory(f"sqlite:///{tmp_path / 'rename.db'}")
    Base.metadata.create_all(session_factory.kw["bind"])
    storage = InMemoryStorage({})
    old = []
    with session_factory.begin() as db:
        for number in (1, 2):
            submission = FormSubmission(
                submission_id=f"public-{number}", form_slug="form", imiona="Anna",
                nazwisko="Nowak", selected_trainings=json.dumps([{"id": "a", "name": "Łódź"}]),
                declaration_signed_filename=f"old-{number}.pdf",
                document_states={"workflow_documents": {"paper": {"documents": {
                    "declaration": {"uploaded_filename": f"old-{number}.pdf",
                                    "generated_filename": "generated.pdf"}}}}},
                data_json={"_signed_declaration_snapshot": {"document_filename": f"old-{number}.pdf"}},
            )
            db.add(submission)
            db.flush()
            path = f"output/form/pdf/deklaracja/podpisane/old-{number}.pdf"
            content = f"%PDF-1.4\nparticipant {number}".encode()
            storage.saved_pdfs[path] = content
            file = SubmissionFile(
                submission_id=submission.id, public_submission_id=submission.submission_id,
                form_slug="form", document_id="declaration", document_type="signed_declaration",
                filename=f"old-{number}.pdf", original_filename=f"upload-{number}.pdf",
                storage_path=path, checksum_sha256=sha256(content).hexdigest(),
                uploaded_by_source="participant", status="signed", signed=True,
            )
            db.add(file)
            old.append((path, content))
        db.add(SubmissionFile(
            submission_id=submission.id, public_submission_id=submission.submission_id,
            form_slug="form", document_id="declaration", document_type="declaration",
            filename="generated.pdf", storage_path="output/form/pdf/deklaracja/niepodpisane/generated.pdf",
            uploaded_by_source="system", status="generated",
        ))
    return UploadedDocumentRenamer(session_factory, storage), session_factory, storage, old


def test_rename_dry_run_apply_verify_rollback_and_collision(tmp_path):
    renamer, sessions, storage, old = _environment(tmp_path)
    with pytest.raises(RenameError, match="No entries"):
        renamer.verify("missing")
    before = dict(storage.saved_pdfs)
    preview = renamer.dry_run()
    assert len(preview["candidates"]) == 2 and not preview["errors"]
    assert storage.saved_pdfs == before
    with sessions() as db:
        assert db.query(UploadedDocumentRename).count() == 0
    result = renamer.apply("run_1")
    assert result["applied"] == 2 and not result["errors"]
    assert renamer.verify("run_1")["verified"] == 2
    assert renamer.apply("run_1")["skipped"] >= 2
    with sessions() as db:
        files = db.query(SubmissionFile).filter(SubmissionFile.uploaded_by_source == "participant").order_by(SubmissionFile.id).all()
        assert [file.filename for file in files] == [
            "Anna_Nowak-deklaracja-Łódź.pdf", "Anna_Nowak-deklaracja-Łódź-2.pdf",
        ]
        assert [file.original_filename for file in files] == ["upload-1.pdf", "upload-2.pdf"]
        for file, (_, content) in zip(files, old):
            assert storage.read_bytes(file.storage_path) == content
            assert DocumentStorageService().read_document_bytes(
                storage=storage, slug="form", filename=file.filename,
                metadata={"storage_path": file.storage_path}, strict_metadata=True,
            ) == content
            assert file.checksum_sha256 == sha256(content).hexdigest()
            submission = db.get(FormSubmission, file.submission_id)
            assert submission.declaration_signed_filename == file.filename
            assert submission.document_states["workflow_documents"]["paper"]["documents"]["declaration"]["uploaded_filename"] == file.filename
            assert submission.document_states["workflow_documents"]["paper"]["documents"]["declaration"]["generated_filename"] == "generated.pdf"
            assert submission.data_json["_signed_declaration_snapshot"]["document_filename"] == file.filename
        assert db.query(SubmissionFile).filter(SubmissionFile.status == "generated").one().filename == "generated.pdf"
        assert db.query(SubmissionWorkflowEvent).filter(SubmissionWorkflowEvent.source == "UPLOADED_DOCUMENT_RENAMED").count() == 2
    rollback = renamer.rollback("run_1")
    assert rollback["rolled_back"] == 2 and not rollback["errors"]
    assert renamer.rollback("run_1")["skipped"] == 2
    assert renamer.verify("run_1")["verified"] == 2
    with sessions() as db:
        files = db.query(SubmissionFile).filter(SubmissionFile.uploaded_by_source == "participant").order_by(SubmissionFile.id).all()
        assert [file.filename for file in files] == ["old-1.pdf", "old-2.pdf"]
        assert db.query(SubmissionWorkflowEvent).filter(SubmissionWorkflowEvent.source == "UPLOADED_DOCUMENT_RENAME_ROLLED_BACK").count() == 2
    assert all(storage.read_bytes(path) == content for path, content in old)
    assert all(storage.exists(entry.new_storage_path) for entry in _journals(sessions))


def _journals(sessions):
    with sessions() as db:
        entries = db.query(UploadedDocumentRename).order_by(UploadedDocumentRename.id).all()
        for entry in entries:
            db.expunge(entry)
        return entries


def _shared_agreement(tmp_path):
    sessions = create_session_factory(f"sqlite:///{tmp_path / 'shared-rename.db'}")
    Base.metadata.create_all(sessions.kw["bind"])
    path = "output/form/pdf/umowy/podpisane/shared.pdf"
    content = b"%PDF-1.4\nshared signed agreement"
    digest = sha256(content).hexdigest()
    storage = InMemoryStorage({})
    storage.saved_pdfs[path] = content
    with sessions.begin() as db:
        submission = FormSubmission(
            submission_id="shared-public", form_slug="form", imiona="Anna", nazwisko="Nowak",
            selected_trainings=json.dumps([{"id": "course", "name": "Łódź"}]),
            agreement_signed_filename="shared.pdf",
            training_agreements=json.dumps([{"training_id": "course", "signed_filename": "shared.pdf"}]),
            document_states={"documents": {"agreement": {"uploaded_filename": "shared.pdf"}}},
        )
        db.add(submission)
        db.flush()
        for document_id, document_type, checksum in (
            ("agreement", "signed_agreement", digest),
            ("training_agreement", "signed_training_agreement", None),
        ):
            db.add(SubmissionFile(
                submission_id=submission.id, public_submission_id=submission.submission_id,
                form_slug="form", document_id=document_id, document_type=document_type,
                filename="shared.pdf", original_filename="participant.pdf", storage_path=path,
                checksum_sha256=checksum, training_key="course", uploaded_by_source="participant",
                status="signed", signed=True,
            ))
    return UploadedDocumentRenamer(sessions, storage), sessions, storage, path, digest


def test_shared_pdf_migrates_once_and_rolls_back_all_references(tmp_path):
    renamer, sessions, storage, old_path, digest = _shared_agreement(tmp_path)
    preview = renamer.dry_run()
    assert len(preview["candidates"]) == 1 and len(preview["candidates"][0]["file_ids"]) == 2
    assert not preview["errors"]
    result = renamer.apply("shared")
    assert result["applied"] == 1 and not result["errors"], result
    journals = _journals(sessions)
    assert len(journals) == 2 and journals[0].group_id == journals[1].group_id
    assert len({item.new_storage_path for item in journals}) == 1
    assert len(storage.saved_pdfs) == 2
    assert renamer.verify("shared")["verified"] == 1
    with sessions() as db:
        files = db.query(SubmissionFile).order_by(SubmissionFile.id).all()
        assert [file.filename for file in files] == ["Anna_Nowak-umowa-Łódź.pdf"] * 2
        assert [file.storage_path for file in files] == [journals[0].new_storage_path] * 2
        assert [file.checksum_sha256 for file in files] == [digest, ""]
        assert storage.read_bytes(files[0].storage_path) == storage.read_bytes(old_path)
        submission = db.get(FormSubmission, files[0].submission_id)
        assert submission.agreement_signed_filename == files[0].filename
        assert json.loads(submission.training_agreements)[0]["signed_filename"] == files[0].filename
        assert submission.document_states["documents"]["agreement"]["uploaded_filename"] == files[0].filename
        assert db.query(SubmissionWorkflowEvent).filter(
            SubmissionWorkflowEvent.source == "UPLOADED_DOCUMENT_RENAMED").count() == 2
    assert renamer.apply("shared")["skipped"] == 1
    rollback = renamer.rollback("shared")
    assert rollback["rolled_back"] == 1 and not rollback["errors"]
    assert renamer.verify("shared")["verified"] == 1
    with sessions() as db:
        files = db.query(SubmissionFile).order_by(SubmissionFile.id).all()
        assert [file.filename for file in files] == ["shared.pdf"] * 2
        assert [file.storage_path for file in files] == [old_path] * 2
        assert [file.checksum_sha256 for file in files] == [digest, ""]
        submission = db.get(FormSubmission, files[0].submission_id)
        assert submission.agreement_signed_filename == "shared.pdf"
        assert json.loads(submission.training_agreements)[0]["signed_filename"] == "shared.pdf"
        assert submission.document_states["documents"]["agreement"]["uploaded_filename"] == "shared.pdf"
        assert db.query(SubmissionWorkflowEvent).filter(
            SubmissionWorkflowEvent.source == "UPLOADED_DOCUMENT_RENAME_ROLLED_BACK").count() == 2
    assert len(storage.saved_pdfs) == 2


def test_shared_pdf_groups_normalized_storage_paths(tmp_path):
    renamer, sessions, storage, path, _ = _shared_agreement(tmp_path)
    with sessions.begin() as db:
        alias = db.query(SubmissionFile).filter(SubmissionFile.document_id == "training_agreement").one()
        alias.storage_path = path.replace("/", "\\")
    preview = renamer.dry_run()
    assert len(preview["candidates"]) == 1
    assert len(preview["candidates"][0]["file_ids"]) == 2
    assert not preview["errors"]
    result = renamer.apply("normalized_path")
    assert result["applied"] == 1 and not result["errors"]
    assert len(storage.saved_pdfs) == 2
    assert renamer.verify("normalized_path")["verified"] == 1
    assert renamer.rollback("normalized_path")["rolled_back"] == 1
    with sessions() as db:
        alias = db.query(SubmissionFile).filter(SubmissionFile.document_id == "training_agreement").one()
        assert alias.storage_path == path.replace("/", "\\")


def test_shared_pdf_groups_historical_source_labels_on_one_path(tmp_path):
    renamer, sessions, storage, _, _ = _shared_agreement(tmp_path)
    with sessions.begin() as db:
        alias = db.query(SubmissionFile).filter(SubmissionFile.document_id == "training_agreement").one()
        alias.uploaded_by_source = "office"
        alias.document_type = "agreement_signed_by_office"
        alias.original_filename = ""
    preview = renamer.dry_run()
    assert len(preview["candidates"]) == 1 and len(preview["candidates"][0]["file_ids"]) == 2
    assert not preview["errors"]
    result = renamer.apply("historical_source_labels")
    assert result["applied"] == 1 and not result["errors"]
    assert len(storage.saved_pdfs) == 2
    assert renamer.verify("historical_source_labels")["verified"] == 1
    assert renamer.rollback("historical_source_labels")["rolled_back"] == 1


def test_path_normalization_preserves_polish_characters():
    path = "output/form/Łódź/umowa.pdf"
    assert rename_script._storage_path(unicodedata.normalize("NFD", path)) == path


def test_participant_and_office_different_paths_remain_separate(tmp_path):
    renamer, sessions, storage, _, _ = _shared_agreement(tmp_path)
    office_path = "output/form/pdf/umowy/podpisane/office.pdf"
    storage.saved_pdfs[office_path] = b"%PDF-1.4\noffice signed copy"
    with sessions.begin() as db:
        alias = db.query(SubmissionFile).filter(SubmissionFile.document_id == "training_agreement").one()
        alias.storage_path = office_path
        alias.filename = "office.pdf"
        alias.uploaded_by_source = "office"
        alias.document_type = "agreement_signed_by_office"
        submission = db.get(FormSubmission, alias.submission_id)
        submission.document_states = {"documents": {
            "agreement": {"uploaded_filename": "shared.pdf", "office_filename": "office.pdf"}}}
    preview = renamer.dry_run()
    assert len(preview["candidates"]) == 2 and not preview["errors"]
    result = renamer.apply("separate_roles")
    assert result["applied"] == 2 and not result["errors"]
    assert len(storage.saved_pdfs) == 4
    with sessions() as db:
        files = db.query(SubmissionFile).order_by(SubmissionFile.id).all()
        assert files[0].storage_path != files[1].storage_path
        assert {file.filename for file in files} == {
            "Anna_Nowak-umowa-Łódź.pdf", "Anna_Nowak-umowa-Łódź-2.pdf"}
        state = db.query(FormSubmission).one().document_states["documents"]["agreement"]
        assert state["uploaded_filename"] == files[0].filename
        assert state["office_filename"] == files[1].filename
    assert renamer.rollback("separate_roles")["rolled_back"] == 2


def test_shared_pdf_ignores_conflicting_stored_checksums(tmp_path):
    renamer, sessions, storage, _, _ = _shared_agreement(tmp_path)
    with sessions.begin() as db:
        alias = db.query(SubmissionFile).filter(SubmissionFile.document_id == "training_agreement").one()
        alias.checksum_sha256 = "f" * 64
    preview = renamer.dry_run()
    assert len(preview["candidates"]) == 1 and not preview["errors"]
    result = renamer.apply("conflict")
    assert result["applied"] == 1 and not result["errors"], result
    assert renamer.verify("conflict")["verified"] == 1
    with sessions() as db:
        assert db.query(SubmissionFile).filter(
            SubmissionFile.document_id == "training_agreement").one().checksum_sha256 == "f" * 64
    assert len(storage.saved_pdfs) == 2


def test_shared_pdf_migrates_when_all_stored_checksums_are_empty(tmp_path):
    renamer, sessions, storage, _, _ = _shared_agreement(tmp_path)
    with sessions.begin() as db:
        for file in db.query(SubmissionFile).all():
            file.checksum_sha256 = ""
    assert len(renamer.dry_run()["candidates"]) == 1
    result = renamer.apply("no_stored_checksums")
    assert result["applied"] == 1 and not result["errors"]
    assert renamer.verify("no_stored_checksums")["verified"] == 1
    with sessions() as db:
        assert {file.checksum_sha256 for file in db.query(SubmissionFile).all()} == {""}
    assert renamer.rollback("no_stored_checksums")["rolled_back"] == 1


@pytest.mark.parametrize("change,condition", [
    ("path", "FILENAME_REFERENCE_COLLISION"),
    ("training_key", "DIFFERENT_TRAINING"),
    ("document_id", "DIFFERENT_DOCUMENT_KIND"),
    ("submission_id", "DIFFERENT_SUBMISSION"),
])
def test_shared_pdf_rejects_ambiguous_links(tmp_path, change, condition):
    renamer, sessions, storage, _, _ = _shared_agreement(tmp_path)
    with sessions.begin() as db:
        alias = db.query(SubmissionFile).filter(SubmissionFile.document_id == "training_agreement").one()
        if change == "submission_id":
            other = FormSubmission(submission_id="foreign", form_slug="form")
            db.add(other)
            db.flush()
            alias.submission_id = other.id
        elif change == "path":
            alias.storage_path = "output/form/pdf/umowy/podpisane/other.pdf"
        else:
            setattr(alias, change, {"filename": "other.pdf", "training_key": "other",
                                    "document_id": "declaration"}[change])
    result = renamer.apply("ambiguous_shared")
    assert result["applied"] == 0
    assert any(error["reason_code"] == "AMBIGUOUS_REFERENCE"
               and error["condition"] == condition for error in result["errors"])
    assert not _journals(sessions)
    assert len(storage.saved_pdfs) == 1


def test_shared_pdf_resumes_after_move_and_preserves_one_target(tmp_path, monkeypatch):
    renamer, sessions, storage, _, _ = _shared_agreement(tmp_path)
    with sessions() as db:
        file_id = db.query(SubmissionFile.id).order_by(SubmissionFile.id).first()[0]
    entry_id = renamer._prepare(file_id, "shared_resume")
    original = renamer._set_status
    interrupted = False

    def interrupt(_id, status, **kwargs):
        nonlocal interrupted
        if status == "copied" and not interrupted:
            interrupted = True
            raise RuntimeError("interrupted")
        return original(_id, status, **kwargs)

    monkeypatch.setattr(renamer, "_set_status", interrupt)
    with pytest.raises(RuntimeError):
        renamer._copy(entry_id)
    assert {entry.status for entry in _journals(sessions)} == {"moving"}
    monkeypatch.setattr(renamer, "_set_status", original)
    result = renamer.apply("shared_resume")
    assert result["applied"] == 1 and not result["errors"], result
    assert len(storage.saved_pdfs) == 2
    assert renamer.verify("shared_resume")["verified"] == 1


def test_shared_pdf_collision_uses_one_suffixed_target(tmp_path):
    renamer, sessions, storage, old_path, _ = _shared_agreement(tmp_path)
    collided = old_path.replace("shared.pdf", "Anna_Nowak-umowa-Łódź.pdf")
    storage.saved_pdfs[collided] = b"%PDF-1.4\nunrelated"
    result = renamer.apply("shared_collision")
    assert result["applied"] == 1 and not result["errors"]
    journals = _journals(sessions)
    assert {item.new_filename for item in journals} == {"Anna_Nowak-umowa-Łódź-2.pdf"}
    assert storage.read_bytes(collided) == b"%PDF-1.4\nunrelated"
    assert len(storage.saved_pdfs) == 3
    before = dict(storage.saved_pdfs)
    preview = renamer.dry_run()
    assert not preview["candidates"] and not preview["errors"]
    assert preview["skipped"] == 1
    assert renamer.apply("shared_collision")["applied"] == 0
    assert renamer.verify("shared_collision")["verified"] == 1
    assert storage.saved_pdfs == before


def test_shared_pdf_ignores_stale_stored_checksum(tmp_path):
    renamer, sessions, storage, _, _ = _shared_agreement(tmp_path)
    with sessions.begin() as db:
        owner = db.query(SubmissionFile).filter(SubmissionFile.document_id == "agreement").one()
        owner.checksum_sha256 = "a" * 64
    result = renamer.apply("stale_checksum")
    assert result["applied"] == 1 and not result["errors"]
    assert renamer.verify("stale_checksum")["verified"] == 1
    with sessions() as db:
        assert db.query(SubmissionFile).filter(SubmissionFile.document_id == "agreement").one().checksum_sha256 == "a" * 64
    assert len(storage.saved_pdfs) == 2


def test_shared_pdf_rejects_copy_with_different_bytes(tmp_path, monkeypatch):
    renamer, sessions, storage, _, _ = _shared_agreement(tmp_path)
    original_move = storage.move_if_absent

    def corrupt_after_move(source, target):
        moved = original_move(source, target)
        if moved:
            storage.saved_pdfs[target] = b"%PDF-1.4\nchanged copy"
        return moved

    monkeypatch.setattr(storage, "move_if_absent", corrupt_after_move)
    result = renamer.apply("corrupt_copy")
    assert result["applied"] == 0
    assert result["errors"][0]["reason_code"] == "CHECKSUM_MISMATCH"
    with sessions() as db:
        assert {file.filename for file in db.query(SubmissionFile).all()} == {"shared.pdf"}
        assert {entry.status for entry in db.query(UploadedDocumentRename).all()} == {"moving"}


def test_shared_pdf_rollback_is_atomic_when_alias_metadata_changed(tmp_path):
    renamer, sessions, storage, _, _ = _shared_agreement(tmp_path)
    assert renamer.apply("atomic_rollback")["applied"] == 1
    with sessions.begin() as db:
        alias = db.query(SubmissionFile).filter(SubmissionFile.document_id == "training_agreement").one()
        alias.training_key = "changed"
    rollback = renamer.rollback("atomic_rollback")
    assert rollback["rolled_back"] == 0
    assert rollback["errors"][0]["reason_code"] == "AMBIGUOUS_REFERENCE"
    with sessions() as db:
        assert {entry.status for entry in db.query(UploadedDocumentRename).all()} == {"applied"}
        assert {file.filename for file in db.query(SubmissionFile).all()} == {"Anna_Nowak-umowa-Łódź.pdf"}
        submission = db.query(FormSubmission).one()
        assert submission.agreement_signed_filename == "Anna_Nowak-umowa-Łódź.pdf"


def test_shared_pdf_finalize_failure_rolls_back_both_rows_and_can_resume(tmp_path, monkeypatch):
    renamer, sessions, storage, old_path, _ = _shared_agreement(tmp_path)
    original_audit = rename_script._audit
    calls = 0

    def interrupt_audit(db, submission, entry, action):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("interrupted")
        return original_audit(db, submission, entry, action)

    monkeypatch.setattr(rename_script, "_audit", interrupt_audit)
    result = renamer.apply("finalize_resume")
    assert result["applied"] == 0 and len(result["errors"]) == 1
    with sessions() as db:
        assert {file.filename for file in db.query(SubmissionFile).all()} == {"shared.pdf"}
        assert {file.storage_path for file in db.query(SubmissionFile).all()} == {old_path}
        assert {entry.status for entry in db.query(UploadedDocumentRename).all()} == {"copied"}
        assert db.query(FormSubmission).one().agreement_signed_filename == "shared.pdf"
        assert db.query(SubmissionWorkflowEvent).count() == 0
    monkeypatch.setattr(rename_script, "_audit", original_audit)
    result = renamer.apply("finalize_resume")
    assert result["applied"] == 1 and not result["errors"]
    assert renamer.verify("finalize_resume")["verified"] == 1


def test_rename_resumes_after_remote_move_before_status_commit(tmp_path, monkeypatch):
    renamer, sessions, storage, _ = _environment(tmp_path)
    with sessions() as db:
        first_id = db.query(SubmissionFile.id).filter(SubmissionFile.filename == "old-1.pdf").scalar()
    entry_id = renamer._prepare(first_id, "interrupted")
    set_status = renamer._set_status
    failed = False
    def interrupt(entry, status, **kwargs):
        nonlocal failed
        if status == "copied" and not failed:
            failed = True
            raise RuntimeError("interrupted")
        return set_status(entry, status, **kwargs)
    monkeypatch.setattr(renamer, "_set_status", interrupt)
    try:
        renamer._copy(entry_id)
    except RuntimeError:
        pass
    assert renamer._entry(entry_id).status == "moving"
    monkeypatch.setattr(renamer, "_set_status", set_status)
    result = renamer.apply("interrupted")
    assert result["applied"] == 2 and not result["errors"]
    assert renamer.verify("interrupted")["verified"] == 2


def test_rename_does_not_validate_pdf_contents(tmp_path):
    renamer, sessions, storage, old = _environment(tmp_path)
    original = b"historical bytes without a PDF header"
    storage.saved_pdfs[old[0][0]] = original
    result = renamer.apply("opaque_bytes")
    assert result["applied"] == 2 and not result["errors"]
    with sessions() as db:
        file = db.query(SubmissionFile).filter(SubmissionFile.public_submission_id == "public-1").one()
        assert storage.read_bytes(file.storage_path) == original
        assert file.checksum_sha256 == sha256(old[0][1]).hexdigest()


def test_rename_skips_file_without_submission_file_record(tmp_path):
    renamer, sessions, storage, _ = _environment(tmp_path)
    with sessions.begin() as db:
        submission = FormSubmission(
            submission_id="legacy", form_slug="form", imiona="Anna", nazwisko="Nowak",
            selected_trainings=json.dumps([{"id": "course", "name": "Łódź"}]),
            training_agreements=json.dumps([{"training_id": "course", "filename": "generated.pdf",
                                             "signed_filename": "legacy-agreement.pdf"}]),
        )
        db.add(submission)
    old_path = "output/form/pdf/umowy/podpisane/legacy-agreement.pdf"
    storage.saved_pdfs[old_path] = b"%PDF-1.4\nlegacy agreement"
    preview = renamer.dry_run()
    assert len(preview["candidates"]) == 2 and not preview["errors"]
    with sessions() as db:
        assert db.query(SubmissionFile).filter(SubmissionFile.public_submission_id == "legacy").count() == 0
    result = renamer.apply("legacy_run")
    assert result["applied"] == 2 and not result["errors"]
    with sessions() as db:
        assert db.query(SubmissionFile).filter(SubmissionFile.public_submission_id == "legacy").count() == 0
        submission = db.query(FormSubmission).filter(FormSubmission.submission_id == "legacy").one()
        assert json.loads(submission.training_agreements)[0]["signed_filename"] == "legacy-agreement.pdf"
        assert json.loads(submission.training_agreements)[0]["filename"] == "generated.pdf"
    assert storage.read_bytes(old_path) == b"%PDF-1.4\nlegacy agreement"


def test_dry_run_does_not_read_pdf_bytes(tmp_path, monkeypatch):
    renamer, _, storage, _ = _environment(tmp_path)

    def no_read(_path):
        raise AssertionError("dry-run must not read document contents")

    monkeypatch.setattr(storage, "read_bytes", no_read)
    report = renamer.dry_run()
    assert len(report["candidates"]) == 2 and not report["errors"]


def test_rename_uses_physical_path_when_metadata_filename_differs(tmp_path):
    renamer, sessions, _, _ = _environment(tmp_path)
    with sessions.begin() as db:
        file = db.query(SubmissionFile).filter(SubmissionFile.filename == "old-1.pdf").one()
        file.filename = "other.pdf"
    report = renamer.apply("mismatched_filename")
    assert report["applied"] == 2 and not report["errors"]
    with sessions() as db:
        file = db.query(SubmissionFile).filter(SubmissionFile.public_submission_id == "public-1").one()
        assert file.filename == "Anna_Nowak-deklaracja-Łódź.pdf"
        submission = db.get(FormSubmission, file.submission_id)
        assert submission.declaration_signed_filename == file.filename
    assert renamer.rollback("mismatched_filename")["rolled_back"] == 2
    with sessions() as db:
        file = db.query(SubmissionFile).filter(SubmissionFile.public_submission_id == "public-1").one()
        assert file.filename == "other.pdf"
        assert db.get(FormSubmission, file.submission_id).declaration_signed_filename == "old-1.pdf"


def test_rename_includes_office_uploaded_agreement(tmp_path):
    renamer, sessions, storage, old = _environment(tmp_path)
    with sessions.begin() as db:
        file = db.query(SubmissionFile).filter(SubmissionFile.filename == "old-1.pdf").one()
        file.document_id = "agreement"
        file.document_type = "agreement_signed_by_office"
        file.uploaded_by_source = "office"
        file.original_filename = "office-upload.pdf"
        submission = db.get(FormSubmission, file.submission_id)
        submission.declaration_signed_filename = ""
        submission.agreement_signed_filename = file.filename
        submission.document_states = {"documents": {"agreement": {"office_filename": file.filename}}}
        submission.data_json = {}
    result = renamer.apply("office_upload")
    assert result["applied"] == 2 and not result["errors"]
    with sessions() as db:
        file = db.query(SubmissionFile).filter(SubmissionFile.public_submission_id == "public-1").one()
        assert file.filename == "Anna_Nowak-umowa-Łódź.pdf"
        assert storage.read_bytes(file.storage_path) == old[0][1]
        assert file.original_filename == "office-upload.pdf"
    assert renamer.rollback("office_upload")["rolled_back"] == 2


def test_rename_includes_archived_submissions_and_default_cli_is_dry_run(tmp_path, monkeypatch, capsys):
    renamer, sessions, storage, _ = _environment(tmp_path)
    with sessions.begin() as db:
        submission = db.query(FormSubmission).filter(FormSubmission.submission_id == "public-1").one()
        submission.deleted_at = datetime.now(timezone.utc)
    before = dict(storage.saved_pdfs)
    monkeypatch.setattr(rename_script.Config, "DATABASE_URL", str(sessions.kw["bind"].url))
    monkeypatch.setattr(rename_script, "create_session_factory", lambda _url: sessions)
    monkeypatch.setattr(rename_script, "create_nextcloud_storage_from_env", lambda: storage)
    assert rename_script.main([]) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "dry-run"
    assert storage.saved_pdfs == before
    assert renamer.apply("archived")["applied"] == 2
    assert renamer.verify("archived")["verified"] == 2


def test_rename_refuses_ambiguous_filename_within_submission(tmp_path):
    renamer, sessions, storage, _ = _environment(tmp_path)
    with sessions.begin() as db:
        file = db.query(SubmissionFile).filter(SubmissionFile.filename == "old-1.pdf").one()
        db.add(SubmissionFile(
            submission_id=file.submission_id, public_submission_id=file.public_submission_id,
            form_slug=file.form_slug, document_id="agreement", document_type="signed_agreement",
            filename=file.filename, storage_path="output/form/pdf/umowy/podpisane/old-1.pdf",
            uploaded_by_source="participant", status="signed",
        ))
    result = renamer.apply("ambiguous")
    assert any(error.get("condition") == "FILENAME_REFERENCE_COLLISION" for error in result["errors"])
    with sessions() as db:
        assert db.query(SubmissionFile).filter(SubmissionFile.filename == "old-1.pdf").count() == 2


def test_rollback_refuses_missing_old_copy(tmp_path):
    renamer, sessions, storage, old = _environment(tmp_path)
    assert renamer.apply("missing_old")["applied"] == 2
    storage.delete(old[0][0])
    result = renamer.rollback("missing_old")
    assert len(result["errors"]) == 1
    with sessions() as db:
        file = db.query(SubmissionFile).filter(SubmissionFile.public_submission_id == "public-1").filter(
            SubmissionFile.uploaded_by_source == "participant").one()
        assert file.filename == "Anna_Nowak-deklaracja-Łódź.pdf"


def test_rollback_recovers_if_new_copy_is_missing(tmp_path):
    renamer, sessions, storage, old = _environment(tmp_path)
    assert renamer.apply("missing_new")["applied"] == 2
    storage.delete(_journals(sessions)[0].new_storage_path)
    result = renamer.rollback("missing_new")
    assert result["rolled_back"] == 2 and not result["errors"]
    assert renamer.verify("missing_new")["verified"] == 2
    assert all(storage.read_bytes(path) == content for path, content in old)


def test_verify_detects_corrupt_new_copy_without_changing_metadata(tmp_path):
    renamer, sessions, storage, _ = _environment(tmp_path)
    assert renamer.apply("verify_corruption")["applied"] == 2
    first = _journals(sessions)[0]
    storage.saved_pdfs[first.new_storage_path] = b"%PDF-1.4\ncorrupt"
    report = renamer.verify("verify_corruption")
    assert len(report["errors"]) == 1
    with sessions() as db:
        file = db.get(SubmissionFile, first.submission_file_id)
        assert file.filename == first.new_filename
    assert renamer.rollback("verify_corruption")["rolled_back"] == 2


def test_reference_replace_only_touches_filename_fields():
    value = {"uploaded_filename": "old.pdf", "generated_filename": "old.pdf",
             "label": "old.pdf", "files": [{"signed_filename": "old.pdf"}]}
    changed = _nested_references(value, "old.pdf", "new.pdf", "old/path", "new/path")
    assert changed["uploaded_filename"] == "new.pdf"
    assert changed["generated_filename"] == "old.pdf"
    assert changed["label"] == "old.pdf"
    assert changed["files"][0]["signed_filename"] == "new.pdf"


def test_candidate_classification_excludes_generated_and_system_files():
    base = dict(document_id="agreement", document_type="agreement", original_filename="upload.pdf",
                uploaded_by_source="participant", status="uploaded")
    assert _document_id(SimpleNamespace(**base)) == "agreement"
    assert _document_id(SimpleNamespace(**{**base, "status": "generated"})) is None
    assert _document_id(SimpleNamespace(**{**base, "uploaded_by_source": "office"})) == "agreement"
    assert _document_id(SimpleNamespace(**{**base, "uploaded_by_source": "office", "original_filename": ""})) is None
    assert _document_id(SimpleNamespace(**{**base, "uploaded_by_source": "system"})) is None
    assert _document_id(SimpleNamespace(**{**base, "document_id": "", "uploaded_by_source": ""})) == "agreement"


def test_journal_ddl_compiles_for_postgresql_and_mariadb():
    for dialect in (postgresql.dialect(), mysql.dialect()):
        sql = str(CreateTable(UploadedDocumentRename.__table__).compile(dialect=dialect))
        assert "uploaded_document_renames" in sql
        assert "submission_file_id" in sql


def test_alembic_revision_creates_and_removes_empty_journal(monkeypatch):
    revision = importlib.import_module("migrations.versions.20261126_0052_uploaded_document_renames")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(revision, "op", operations)
        revision.upgrade()
        assert inspect(connection).has_table("uploaded_document_renames")
        revision.downgrade()
        assert not inspect(connection).has_table("uploaded_document_renames")


def test_nextcloud_move_refuses_overwrite(monkeypatch):
    storage = NextcloudStorage("https://cloud.test", "user", "secret")
    requests = []
    statuses = iter((201, 412))
    def request(method, path, **kwargs):
        requests.append((method, path, kwargs))
        return SimpleNamespace(status_code=next(statuses))
    monkeypatch.setattr(storage, "_request", request)
    assert storage.move_if_absent("output/staged.pdf", "output/Żaneta.pdf")
    assert not storage.move_if_absent("output/staged.pdf", "output/Żaneta.pdf")
    assert all(item[0] == "MOVE" and item[2]["headers"]["Overwrite"] == "F" for item in requests)
    assert "%C5%BBaneta.pdf" in requests[0][2]["headers"]["Destination"]


def test_rename_excludes_rejected_documents(tmp_path):
    renamer, sessions, storage, old = _environment(tmp_path)

    with sessions.begin() as db:
        file = db.query(SubmissionFile).filter(
            SubmissionFile.filename == "old-1.pdf"
        ).one()

        file.status = "rejected"
        file.document_id = "agreement"
        file.document_type = "uploaded_agreement_participant"
        file.uploaded_by_source = ""
        submission = db.get(FormSubmission, file.submission_id)
        submission.declaration_signed_filename = ""
        submission.agreement_signed_filename = file.filename
        submission.document_states = {"documents": {"agreement": {"uploaded_filename": file.filename}}}
        submission.data_json = {}

    preview = renamer.dry_run()

    assert len(preview["candidates"]) == 1
    assert not preview["errors"]

    result = renamer.apply("rejected_documents")

    assert result["applied"] == 1
    assert not result["errors"]

    with sessions() as db:
        file = db.query(SubmissionFile).filter(
            SubmissionFile.filename == "old-1.pdf"
        ).one()

        assert file.status == "rejected"
        assert file.storage_path == old[0][0]
        assert storage.read_bytes(file.storage_path) == old[0][1]



def test_submission_driven_ignores_unreferenced_uploaded_files(tmp_path):
    renamer, sessions, storage, _ = _environment(tmp_path)
    path = "output/form/pdf/umowy/podpisane/historical.pdf"
    storage.saved_pdfs[path] = b"historical upload"
    with sessions.begin() as db:
        submission = db.query(FormSubmission).filter_by(submission_id="public-1").one()
        db.add(SubmissionFile(submission_id=submission.id, public_submission_id=submission.submission_id,
            form_slug=submission.form_slug, document_id="agreement", document_type="signed_agreement",
            filename="historical.pdf", storage_path=path, original_filename="old.pdf",
            uploaded_by_source="participant", status="signed"))
    preview = renamer.dry_run()
    assert preview["submissions_checked"] == 2
    assert len(preview["candidates"]) == 2 and not preview["errors"]
    assert renamer.apply("submission_driven")["applied"] == 2
    with sessions() as db:
        assert db.query(SubmissionFile).filter_by(filename="historical.pdf").one().storage_path == path


def test_submission_driven_groups_rejected_alias_of_active_agreement(tmp_path):
    renamer, sessions, storage, path, _ = _shared_agreement(tmp_path)
    with sessions.begin() as db:
        alias = db.query(SubmissionFile).filter_by(document_id="training_agreement").one()
        alias.document_type = "uploaded_agreement_participant"
        alias.status = "rejected"
    preview = renamer.dry_run()
    assert len(preview["candidates"]) == 1 and not preview["errors"]
    assert len(preview["candidates"][0]["file_ids"]) == 2
    result = renamer.apply("active_with_rejected_alias")
    assert result["applied"] == 1 and not result["errors"]
    with sessions() as db:
        alias = db.query(SubmissionFile).filter_by(document_id="training_agreement").one()
        assert alias.status == "rejected"
        assert alias.storage_path != path
    assert renamer.rollback("active_with_rejected_alias")["rolled_back"] == 1


def test_submission_driven_reads_training_key_from_workflow(tmp_path):
    renamer, sessions, _, _ = _environment(tmp_path)
    with sessions.begin() as db:
        file = db.query(SubmissionFile).filter_by(filename="old-1.pdf").one()
        submission = db.get(FormSubmission, file.submission_id)
        submission.selected_trainings = json.dumps([{"id": "a", "name": "Excel"}, {"id": "b", "name": "Python"}])
        file.document_id = "training_agreement"
        file.document_type = "signed_training_agreement"
        submission.declaration_signed_filename = ""
        submission.data_json = {}
        submission.document_states = {"workflow_documents": {"paper": {"documents": {
            "training_agreement": {"instances": {"b": {"uploaded_filename": file.filename}}}}}}}
    with sessions() as db:
        submission = db.query(FormSubmission).filter_by(submission_id="public-1").one()
        plans, unresolved = rename_script._submission_plan(db, submission)
        assert plans[0][1:] == ("agreement", "b") and not unresolved
    result = renamer.apply("training_from_workflow")
    assert result["applied"] == 2 and not result["errors"]
    with sessions() as db:
        file = db.query(SubmissionFile).filter_by(public_submission_id="public-1").one()
        assert file.filename == "Anna_Nowak-umowa-Python.pdf"


def test_submission_driven_reads_signed_training_file_link(tmp_path):
    renamer, sessions, _, _ = _environment(tmp_path)
    with sessions.begin() as db:
        file = db.query(SubmissionFile).filter_by(filename="old-1.pdf").one()
        submission = db.get(FormSubmission, file.submission_id)
        file.document_id = "agreement"
        file.document_type = "signed_agreement"
        submission.declaration_signed_filename = ""
        submission.document_states = {}
        submission.data_json = {}
        submission.selected_trainings = json.dumps([{"id": "course", "name": "Łódź"}])
        db.add(rename_script.SubmissionTraining(submission_id=submission.id, training_id="course",
            training_name_snapshot="Łódź", agreement_file_id=file.id,
            signed_agreement_uploaded_at=datetime.now(timezone.utc)))
    result = renamer.apply("training_link")
    assert result["applied"] == 2 and not result["errors"]
    with sessions() as db:
        training = db.query(rename_script.SubmissionTraining).one()
        file = db.get(SubmissionFile, training.agreement_file_id)
        assert file.filename == "Anna_Nowak-umowa-Łódź.pdf"


def test_submission_driven_automatic_runs_twice(tmp_path, monkeypatch):
    from contextlib import nullcontext
    renamer, _, storage, _ = _environment(tmp_path)
    monkeypatch.setattr(rename_script, "_deployment_lock", lambda engine: nullcontext())
    first = rename_script._automatic_migration(renamer)
    assert not first["errors"] and first["result"]["applied"] == 2
    before = dict(storage.saved_pdfs)
    preview = renamer.dry_run()
    assert not preview["candidates"] and not preview["errors"]
    assert preview["skipped"] == 2
    second = rename_script._automatic_migration(renamer)
    assert not second["errors"] and second["result"]["applied"] == 0
    assert storage.saved_pdfs == before


def test_dry_run_reports_missing_applied_target(tmp_path):
    renamer, sessions, storage, _, _ = _shared_agreement(tmp_path)
    assert renamer.apply("missing_target")["applied"] == 1
    target = _journals(sessions)[0].new_storage_path
    del storage.saved_pdfs[target]
    preview = renamer.dry_run()
    assert not preview["candidates"]
    assert any(error["reason_code"] == "SOURCE_MISSING" for error in preview["errors"])


def test_dry_run_reports_changed_applied_journal(tmp_path):
    renamer, sessions, _, _ = _environment(tmp_path)
    assert renamer.apply("changed_journal")["applied"] == 2
    with sessions.begin() as db:
        entry = db.query(UploadedDocumentRename).order_by(UploadedDocumentRename.id).first()
        entry.new_filename = "wrong.pdf"
    preview = renamer.dry_run()
    assert not preview["candidates"]
    assert any(error["reason_code"] == "METADATA_CHANGED" for error in preview["errors"])


def test_auto_rejects_disabled_tls(tmp_path, monkeypatch):
    renamer, _, storage, _ = _environment(tmp_path)
    monkeypatch.setattr(storage, "verify_ssl", False, raising=False)
    with pytest.raises(RenameError) as error:
        rename_script._automatic_migration(renamer)
    assert error.value.code == "TLS_VERIFICATION_DISABLED"
