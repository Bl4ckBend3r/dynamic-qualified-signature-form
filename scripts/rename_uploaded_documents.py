"""Rename stored uploaded declarations and agreements without modifying PDF bytes.

Run from the project environment. With no action flag this prints a dry run.
Apply prints a migration ID; pass it again to resume or to verify/rollback.
Old and new copies are intentionally retained for safe rollback.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import warnings
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import inspect, select, text
from urllib3.exceptions import InsecureRequestWarning

from config import Config
from database import create_session_factory
from models import FormSubmission, SubmissionFile, SubmissionTraining, SubmissionWorkflowEvent, UploadedDocumentRename
from services.document_naming_service import build_uploaded_document_filename, uploaded_filename_with_suffix
from services.documents.document_storage_service import validate_storage_path
from services.documents.document_workflow_service import DocumentWorkflowService
from services.nextcloud_storage import create_nextcloud_storage_from_env


AUTO_MIGRATION_ID = "submission-document-names-v1"

DOCUMENT_IDS = {"declaration", "agreement", "training_agreement"}
SIGNED_TYPES = {
    "signed_declaration": "declaration",
    "signed_agreement": "agreement",
    "signed_training_agreement": "training_agreement",
    "agreement_signed_by_office": "agreement",
}
REFERENCE_KEYS = {"filename", "signed_filename", "uploaded_filename", "office_filename",
                  "office_source_filename", "document_filename", "storage_path"}
ACTIVE_STATUSES = {"planned", "staged", "moving", "copied", "applied"}


class RenameError(RuntimeError):
    def __init__(self, message: str, code: str = "MIGRATION_ERROR", *, condition: str | None = None):
        super().__init__(message)
        self.code = code
        self.condition = condition


def _reason(exc: Exception) -> str:
    return exc.code if isinstance(exc, RenameError) else "UNEXPECTED_ERROR"


def _diagnostic(exc: Exception) -> dict:
    result = {"reason": type(exc).__name__, "reason_code": _reason(exc)}
    if isinstance(exc, RenameError) and exc.condition:
        result["condition"] = exc.condition
    return result


def _storage_path(path: str) -> str:
    """Use one safe, NFC WebDAV path for all spelling variants of a file."""
    try:
        normalized = validate_storage_path(path)
    except ValueError as exc:
        raise RenameError("Invalid document storage path", "AMBIGUOUS_REFERENCE",
                          condition="INVALID_STORAGE_PATH") from exc
    return validate_storage_path(unicodedata.normalize("NFC", str(PurePosixPath(normalized))))


def _document_id(file: SubmissionFile) -> str | None:
    if file.status in {"generated", "rejected"} or file.uploaded_by_source == "system":
        return None
    document_id = str(file.document_id or "")
    document_type = str(file.document_type or "")
    if file.uploaded_by_source == "office" and not file.original_filename and document_type not in SIGNED_TYPES:
        return None
    if document_id and document_id not in DOCUMENT_IDS:
        return None
    if document_id in DOCUMENT_IDS:
        if (file.uploaded_by_source in {"participant", "office"} or document_type in SIGNED_TYPES
                or bool(file.original_filename)):
            return document_id
    if document_type in SIGNED_TYPES:
        return SIGNED_TYPES[document_type]
    if (document_type in DOCUMENT_IDS
            and (file.uploaded_by_source in {"participant", "office"} or file.original_filename)):
        return document_type
    return None


def _submission(db, file: SubmissionFile) -> FormSubmission:
    submission = db.execute(
        select(FormSubmission).where(FormSubmission.id == file.submission_id)
        .execution_options(include_deleted_submissions=True)
    ).scalar_one()
    return submission


def _locked_file_and_submission(db, file_id: int):
    submission_id = db.execute(select(SubmissionFile.submission_id).where(
        SubmissionFile.id == file_id,
    )).scalar_one()
    submission = db.execute(select(FormSubmission).where(
        FormSubmission.id == submission_id,
    ).with_for_update().execution_options(include_deleted_submissions=True)).scalar_one()
    file = db.execute(select(SubmissionFile).where(
        SubmissionFile.id == file_id,
    ).with_for_update()).scalar_one()
    if file.submission_id != submission.id:
        raise RenameError("Document submission changed during migration")
    return file, submission


def _filename_for(file: SubmissionFile, submission: FormSubmission, session_factory, *, kind: str, training_key: str) -> str:
    # The document kind and training association come from the submission's
    # references. The file row supplies its physical location, not its name.
    row = {
        "id": submission.id, "imiona": submission.imiona, "nazwisko": submission.nazwisko,
        "selected_trainings": submission.selected_trainings,
    }
    workflow = DocumentWorkflowService(SimpleNamespace(
        submission_repository=SimpleNamespace(session_factory=session_factory)
    ))
    source = {"training_key": training_key} if training_key else None
    names = workflow._uploaded_training_names(row, kind, source)
    return build_uploaded_document_filename(row, kind, names)


def _path_with_filename(old_path: str, filename: str) -> str:
    path = _storage_path(old_path)
    return _storage_path(str(PurePosixPath(path).parent / filename))


def _read_verified(storage, path: str, expected_hash: str) -> bytes:
    data = storage.read_bytes(path)
    if sha256(data).hexdigest() != expected_hash:
        raise RenameError("Stored copy differs from migration source", "CHECKSUM_MISMATCH")
    return data


def _kind(value) -> str | None:
    value = str(value or "")
    value = SIGNED_TYPES.get(value, value)
    value = {
        "uploaded_agreement_participant": "agreement",
        "uploaded_training_agreement_participant": "agreement",
        "uploaded_declaration_participant": "declaration",
    }.get(value, value)
    if value in {"agreement", "training_agreement"}:
        return "agreement"
    return "declaration" if value == "declaration" else None


def _family(file: SubmissionFile) -> str | None:
    # Historical rejected/unsigned aliases can share an accepted physical file.
    # Do not use candidate eligibility to decide the alias's document kind.
    if file.status == "generated" or file.uploaded_by_source == "system":
        return None
    by_id = _kind(file.document_id)
    by_type = _kind(file.document_type)
    if file.document_id and not by_id:
        return None
    if file.document_type and not by_type:
        return None
    if by_id and by_type and by_id != by_type:
        return None
    return by_id or by_type


def _submission_references(submission: FormSubmission):
    """Read accepted-document references from the submission, not filenames' spelling.

    Only uploaded/signed/office fields are considered. Generic filename fields
    in training agreement lists and workflow files describe generated PDFs.
    """
    refs = set()

    def add(kind, training, value):
        if kind and isinstance(value, str) and value.strip():
            refs.add((kind, str(training or ""), value))

    add("declaration", "", submission.declaration_signed_filename)
    add("agreement", "", submission.agreement_signed_filename)
    snapshot = (submission.data_json or {}).get("_signed_declaration_snapshot")
    if isinstance(snapshot, dict):
        add("declaration", "", snapshot.get("document_filename"))

    def walk(node, kind=None, training=""):
        if isinstance(node, list):
            for item in node:
                walk(item, kind, training)
        elif isinstance(node, dict):
            explicit_kind = _kind(node["document_id"]) if node.get("document_id") else kind
            training = str(node.get("training_key") or node.get("training_id") or training or "")
            for key in ("signed_filename", "uploaded_filename", "office_filename"):
                add(explicit_kind, training, node.get(key))
            for key, value in node.items():
                if key == "instances" and isinstance(value, dict):
                    for instance_key, instance in value.items():
                        walk(instance, explicit_kind, str(instance_key))
                elif key not in {"files", "instances"} and isinstance(value, (dict, list)):
                    walk(value, _kind(key) or explicit_kind, training)

    walk(submission.document_states or {})
    if submission.training_agreements:
        try:
            agreements = json.loads(submission.training_agreements)
        except (ValueError, TypeError) as exc:
            raise RenameError("Invalid training agreement JSON", "INVALID_SUBMISSION_REFERENCES") from exc
        walk(agreements, "agreement")
    return sorted(refs)


def _submission_plan(db, submission: FormSubmission):
    """Resolve business references to physical groups within one submission.

    Unreferenced historical files are left alone. Missing metadata is reported
    separately; never fabricate a path or create a metadata row.
    """
    files = list(db.execute(select(SubmissionFile).where(
        SubmissionFile.submission_id == submission.id
    ).order_by(SubmissionFile.id)).scalars())
    by_id = {file.id: file for file in files}
    normalized = {}
    for file in files:
        try:
            normalized[file.id] = _storage_path(file.storage_path)
        except RenameError:
            # Unrelated malformed historical files are not candidates.
            pass
    refs = [(kind, training, name, None) for kind, training, name in _submission_references(submission)]
    for training in db.execute(select(SubmissionTraining).where(
        SubmissionTraining.submission_id == submission.id,
        SubmissionTraining.agreement_file_id.is_not(None),
        SubmissionTraining.signed_agreement_uploaded_at.is_not(None),
    )).scalars():
        refs.append(("agreement", training.training_id, None, training.agreement_file_id))

    grouped = {}
    unresolved = []
    for kind, training_key, name, referenced_id in refs:
        if referenced_id is not None:
            if referenced_id not in by_id:
                raise RenameError("Training links to a file outside its submission", "AMBIGUOUS_REFERENCE",
                                  condition="DIFFERENT_SUBMISSION")
            matches = [by_id[referenced_id]]
        else:
            matches = [file for file in files if file.filename == name or
                       (file.id in normalized and PurePosixPath(normalized[file.id]).name == name)]
        if not matches:
            unresolved.append({"submission_id": submission.id, "document_id": kind,
                               "condition": "MISSING_FILE_METADATA"})
            continue
        paths = {_storage_path(file.storage_path) for file in matches}
        if len(paths) != 1:
            raise RenameError("Filename refers to different physical files", "AMBIGUOUS_REFERENCE",
                              condition="FILENAME_REFERENCE_COLLISION")
        path = next(iter(paths))
        aliases = [file for file in files if normalized.get(file.id) == path]
        eligible = [file for file in aliases if file.status not in {"generated", "rejected", "superseded"}
                    and file.uploaded_by_source != "system" and _family(file) == kind]
        if not eligible:
            # A referenced rejected-only document must not be promoted to signed.
            if any(_family(file) not in {None, kind} for file in aliases):
                raise RenameError("Submission and file document kinds differ", "AMBIGUOUS_REFERENCE",
                                  condition="DIFFERENT_DOCUMENT_KIND")
            unresolved.append({"submission_id": submission.id, "document_id": kind,
                               "condition": "NO_ACTIVE_FILE_METADATA"})
            continue
        file = eligible[0]
        if training_key and file.training_key and training_key != file.training_key:
            raise RenameError("Submission and file training links differ", "AMBIGUOUS_REFERENCE",
                              condition="DIFFERENT_TRAINING")
        # Compatibility for legacy submission-level references with no training key.
        key = str(training_key or file.training_key or "")
        previous = grouped.get(path)
        if previous:
            if previous[1] != kind:
                raise RenameError("Same path referenced as different documents", "AMBIGUOUS_REFERENCE",
                                  condition="DIFFERENT_DOCUMENT_KIND")
            if previous[2] and key and previous[2] != key:
                raise RenameError("Same path referenced by different trainings", "AMBIGUOUS_REFERENCE",
                                  condition="DIFFERENT_TRAINING")
            key = key or previous[2]
        grouped[path] = (file.id, kind, key)
    for file in files:
        if (normalized.get(file.id) not in grouped and _document_id(file)
                and file.status != "superseded"):
            unresolved.append({"submission_id": submission.id, "file_id": file.id,
                               "condition": "UNREFERENCED_UPLOAD"})
    return sorted(grouped.values()), unresolved


def _submission_ids(session_factory):
    with session_factory() as db:
        return list(db.execute(select(FormSubmission.id).order_by(FormSubmission.id)
            .execution_options(include_deleted_submissions=True)).scalars())


def _plan_for_file(db, submission, file):
    path = _storage_path(file.storage_path)
    plans, _ = _submission_plan(db, submission)
    for file_id, kind, training_key in plans:
        candidate = db.get(SubmissionFile, file_id)
        if _storage_path(candidate.storage_path) == path:
            return kind, training_key
    return None


def _assert_unambiguous(db, file: SubmissionFile, *, lock: bool = False) -> list[SubmissionFile]:
    """Group only records for one physical WebDAV path and compatible document."""
    path = _storage_path(file.storage_path)
    candidate_ids = []
    for row_id, raw_path in db.execute(select(SubmissionFile.id, SubmissionFile.storage_path)):
        try:
            if _storage_path(raw_path) == path:
                candidate_ids.append(row_id)
        except RenameError:
            # An unrelated malformed row cannot identify this physical file.
            continue
    query = select(SubmissionFile).where(SubmissionFile.id.in_(candidate_ids)).order_by(SubmissionFile.id)
    if lock:
        query = query.with_for_update()
    group = list(db.execute(query).scalars())
    family = _family(file)
    if not family:
        raise RenameError("Document kind is not eligible", "AMBIGUOUS_REFERENCE",
                          condition="INELIGIBLE_DOCUMENT_KIND")
    for member in group:
        if member.submission_id != file.submission_id:
            raise RenameError("Physical document belongs to different submissions", "AMBIGUOUS_REFERENCE",
                              condition="DIFFERENT_SUBMISSION")
        if member.training_key != file.training_key:
            raise RenameError("Physical document has different training links", "AMBIGUOUS_REFERENCE",
                              condition="DIFFERENT_TRAINING")
        if _family(member) != family:
            raise RenameError("Physical document has different document kinds", "AMBIGUOUS_REFERENCE",
                              condition="DIFFERENT_DOCUMENT_KIND")
    return group


def _source_bytes(storage, group: list[SubmissionFile]) -> tuple[bytes, str]:
    data = storage.read_bytes(_storage_path(group[0].storage_path))
    return data, sha256(data).hexdigest()


def _nested_references(value, old_filename: str, new_filename: str, old_path: str, new_path: str):
    if isinstance(value, list):
        return [_nested_references(item, old_filename, new_filename, old_path, new_path) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in REFERENCE_KEYS and not str(key).startswith("generated_") and isinstance(item, str):
            result[key] = new_filename if item == old_filename else new_path if item == old_path else item
        else:
            result[key] = _nested_references(item, old_filename, new_filename, old_path, new_path)
    return result


def _replace_submission_references(submission, old_filename, new_filename, old_path, new_path):
    for column in ("declaration_signed_filename", "agreement_signed_filename"):
        if getattr(submission, column) == old_filename:
            setattr(submission, column, new_filename)
    if isinstance(submission.document_states, dict):
        submission.document_states = _nested_references(
            submission.document_states, old_filename, new_filename, old_path, new_path,
        )
    agreements = submission.training_agreements or ""
    if agreements:
        try:
            decoded = json.loads(agreements)
        except (ValueError, TypeError):
            if old_filename in agreements or old_path in agreements:
                raise RenameError("Unparseable training agreement references")
        else:
            replaced = _nested_references(decoded, old_filename, new_filename, old_path, new_path)
            if replaced != decoded:
                submission.training_agreements = json.dumps(replaced, ensure_ascii=False)
    data = deepcopy(submission.data_json or {})
    snapshot = data.get("_signed_declaration_snapshot")
    if isinstance(snapshot, dict) and snapshot.get("document_filename") == old_filename:
        snapshot["document_filename"] = new_filename
        submission.data_json = data


def _has_reference(submission, filename: str, path: str) -> bool:
    if any(getattr(submission, column) == filename for column in
           ("declaration_signed_filename", "agreement_signed_filename")):
        return True
    if isinstance(submission.document_states, dict) and _nested_references(
        submission.document_states, filename, "__renamed__", path, "__moved__",
    ) != submission.document_states:
        return True
    try:
        agreements = json.loads(submission.training_agreements or "[]")
    except (ValueError, TypeError):
        agreements = []
    if _nested_references(agreements, filename, "__renamed__", path, "__moved__") != agreements:
        return True
    snapshot = (submission.data_json or {}).get("_signed_declaration_snapshot") or {}
    return isinstance(snapshot, dict) and snapshot.get("document_filename") == filename



def _assert_reference_names_unambiguous(
    db,
    submission,
    group: list[SubmissionFile],
) -> None:
    """Reject ambiguous references to different physical files."""

    ids = {member.id for member in group}
    path = _storage_path(group[0].storage_path)
    filename = PurePosixPath(path).name

    names = {filename}

    names.update(
        member.filename
        for member in group
        if member.filename
    )

    referenced_names = {
        name
        for name in names
        if _has_reference(submission, name, "\0")
    }

    others = db.execute(
        select(
            SubmissionFile.id,
            SubmissionFile.filename,
            SubmissionFile.storage_path,
        ).where(
            SubmissionFile.submission_id == submission.id,
        )
    ).all()

    for row_id, other_filename, other_path in others:
        if row_id in ids:
            continue

        normalized_path = _storage_path(other_path)

        if normalized_path == path:
            continue

        other_names = {
            other_filename,
            PurePosixPath(normalized_path).name,
        }

        if referenced_names.intersection(other_names):
            raise RenameError(
                "Filename reference points to multiple physical files",
                "AMBIGUOUS_REFERENCE",
                condition="FILENAME_REFERENCE_COLLISION",
            )

    if any(
        member.filename != filename
        and member.filename
        and _has_reference(submission, member.filename, "\0")
        for member in group
    ):
        raise RenameError(
            "Reference uses a different metadata filename",
            "AMBIGUOUS_REFERENCE",
            condition="NONCANONICAL_FILENAME_REFERENCE",
        )

def _audit(db, submission: FormSubmission, entry: UploadedDocumentRename, action: str):
    db.add(SubmissionWorkflowEvent(
        submission_id=submission.id, public_submission_id=submission.submission_id,
        form_slug=submission.form_slug, previous_status=submission.process_status,
        new_status=submission.process_status, previous_step=submission.workflow_step,
        new_step=submission.workflow_step, actor_role="system", source=action,
        reason="Participant PDF filename migration",
        side_effects={"migration_id": entry.migration_id, "rename_journal_id": entry.id,
                      "submission_file_id": entry.submission_file_id},
    ))


class UploadedDocumentRenamer:
    def __init__(self, session_factory, storage):
        self.session_factory = session_factory
        self.storage = storage

    def _require_journal(self):
        engine = self.session_factory.kw["bind"]
        if not inspect(engine).has_table(UploadedDocumentRename.__tablename__):
            raise RenameError("Migration journal table is missing; run alembic upgrade head first")

    def _planned_files(self, report):
        for submission_id in _submission_ids(self.session_factory):
            report["submissions_checked"] += 1
            try:
                with self.session_factory() as db:
                    submission = db.execute(select(FormSubmission).where(
                        FormSubmission.id == submission_id
                    ).execution_options(include_deleted_submissions=True)).scalar_one()
                    plans, unresolved = _submission_plan(db, submission)
                report["unresolved"].extend(unresolved)
            except Exception as exc:
                report["errors"].append({"submission_id": submission_id, **_diagnostic(exc)})
                continue
            for file_id, kind, training_key in plans:
                yield submission_id, file_id, kind, training_key

    def _already_applied(self, db, group: list[SubmissionFile]) -> bool:
        """Recognize journaled targets, including collision suffixes."""
        members = {member.id: member for member in group}
        entries = list(db.execute(select(UploadedDocumentRename).where(
            UploadedDocumentRename.submission_file_id.in_(members),
            UploadedDocumentRename.status == "applied",
        ).order_by(UploadedDocumentRename.id)).scalars())
        if not entries:
            return False
        if len({(item.migration_id, item.group_id) for item in entries}) != 1:
            raise RenameError("Multiple applied journal groups", "INCOMPLETE_JOURNAL")
        entries = self._group_entries(db, entries[0])
        if (len(entries) != len(members)
                or {item.submission_file_id for item in entries} != set(members)
                or any(item.group_id != min(members) or item.status != "applied" for item in entries)):
            raise RenameError("Incomplete applied journal group", "INCOMPLETE_JOURNAL")
        for item in entries:
            member = members[item.submission_file_id]
            if (member.filename, member.storage_path) != (item.new_filename, item.new_storage_path):
                raise RenameError("Document metadata does not match journal", "METADATA_CHANGED")
        if not self.storage.exists(entries[0].new_storage_path):
            raise RenameError("Applied target is missing", "SOURCE_MISSING")
        return True

    def dry_run(self) -> dict:
        self._require_journal()
        report = {"action": "dry-run", "candidates": [], "skipped": 0, "errors": [],
                  "submissions_checked": 0, "unresolved": []}
        for submission_id, file_id, kind, training_key in self._planned_files(report):
            try:
                with self.session_factory() as db:
                    file = db.get(SubmissionFile, file_id)
                    submission = _submission(db, file)
                    group = _assert_unambiguous(db, file)
                    _assert_reference_names_unambiguous(db, submission, group)
                    if self._already_applied(db, group):
                        report["skipped"] += 1
                        continue
                    base = _filename_for(file, submission, self.session_factory, kind=kind, training_key=training_key)
                    old_path = _storage_path(file.storage_path)
                    if old_path == _path_with_filename(old_path, base) and all(member.filename == base for member in group):
                        report["skipped"] += 1
                        continue
                    if not self.storage.exists(old_path):
                        raise RenameError("Source file is missing", "SOURCE_MISSING")
                    report["candidates"].append({"submission_id": submission_id, "file_id": file.id,
                                                 "file_ids": [member.id for member in group],
                                                 "document_id": kind,
                                                 "collision": self.storage.exists(_path_with_filename(old_path, base))})
            except Exception as exc:
                report["errors"].append({"submission_id": submission_id, "file_id": file_id, **_diagnostic(exc)})
        return report

    def _prepare(self, file_id: int, migration_id: str) -> int | None:
        with self.session_factory.begin() as db:
            file, submission = _locked_file_and_submission(db, file_id)
            plan = _plan_for_file(db, submission, file)
            if plan is None:
                return None
            kind, training_key = plan
            group = _assert_unambiguous(db, file, lock=True)
            _assert_reference_names_unambiguous(db, submission, group)
            group_id = min(member.id for member in group)
            ids = [member.id for member in group]
            existing = list(db.execute(select(UploadedDocumentRename).where(
                UploadedDocumentRename.migration_id == migration_id,
                UploadedDocumentRename.submission_file_id.in_(ids),
            ).order_by(UploadedDocumentRename.id).with_for_update()).scalars())
            if existing:
                if {entry.submission_file_id for entry in existing} != set(ids) or any(
                    entry.group_id != group_id for entry in existing
                ):
                    raise RenameError("Incomplete document journal group", "INCOMPLETE_JOURNAL")
                if any(entry.status == "rolled_back" for entry in existing):
                    raise RenameError("Rolled-back migration cannot be reapplied; use a new migration ID")
                return existing[0].id
            other = db.execute(select(UploadedDocumentRename).where(
                UploadedDocumentRename.submission_file_id.in_(ids),
            )).scalars().all()
            if any(entry.status in ACTIVE_STATUSES for entry in other):
                return None
            base = _filename_for(file, submission, self.session_factory, kind=kind, training_key=training_key)
            old_path = _storage_path(file.storage_path)
            if old_path == _path_with_filename(old_path, base) and all(member.filename == base for member in group):
                return None
            _, digest = _source_bytes(self.storage, group)
            staging_name = f".uploaded-document-rename-{uuid4().hex}.pdf"
            for member in group:
                db.add(UploadedDocumentRename(
                    migration_id=migration_id, submission_file_id=member.id, group_id=group_id,
                    old_filename=member.filename, old_storage_path=member.storage_path,
                    base_filename=base, new_filename=base,
                    new_storage_path=_path_with_filename(old_path, base),
                    staging_storage_path=_path_with_filename(old_path, staging_name),
                    checksum_sha256=digest, status="planned",
                ))
            db.flush()
            return db.execute(select(UploadedDocumentRename.id).where(
                UploadedDocumentRename.migration_id == migration_id,
                UploadedDocumentRename.submission_file_id == group_id,
            )).scalar_one()

    @staticmethod
    def _group_entries(db, entry: UploadedDocumentRename, *, lock: bool = False):
        query = select(UploadedDocumentRename).where(
            UploadedDocumentRename.migration_id == entry.migration_id,
            UploadedDocumentRename.group_id == entry.group_id,
        ).order_by(UploadedDocumentRename.id)
        if lock:
            query = query.with_for_update()
        entries = list(db.execute(query).scalars())
        if not entries or len({(_storage_path(item.old_storage_path), item.base_filename,
                               item.new_filename, item.new_storage_path, item.staging_storage_path,
                               item.checksum_sha256, item.status) for item in entries}) != 1:
            raise RenameError("Inconsistent document journal group", "INCOMPLETE_JOURNAL")
        return entries

    def _entry(self, entry_id: int) -> UploadedDocumentRename:
        with self.session_factory() as db:
            entry = db.get(UploadedDocumentRename, entry_id)
            if not entry:
                raise RenameError("Migration journal entry is missing")
            db.expunge(entry)
            return entry

    def _set_status(self, entry_id: int, status: str, *, sequence: int | None = None):
        with self.session_factory.begin() as db:
            entry = db.get(UploadedDocumentRename, entry_id)
            for member in self._group_entries(db, entry, lock=True):
                if sequence is not None:
                    member.new_filename = uploaded_filename_with_suffix(member.base_filename, sequence)
                    member.new_storage_path = _path_with_filename(member.old_storage_path, member.new_filename)
                member.status = status

    def _copy(self, entry_id: int):
        # Serialize workers sharing one migration ID while the WebDAV move is
        # in progress. The durable "moving" state remains committed separately
        # so a process crash can be resumed safely.
        engine = self.session_factory.kw["bind"]
        if engine.dialect.name in {"postgresql", "mysql", "mariadb"}:
            entry = self._entry(entry_id)
            with self.session_factory.begin() as db:
                submission_id = db.execute(select(SubmissionFile.submission_id).where(
                    SubmissionFile.id == entry.submission_file_id,
                )).scalar_one()
                db.execute(select(FormSubmission.id).where(
                    FormSubmission.id == submission_id,
                ).with_for_update().execution_options(include_deleted_submissions=True)).scalar_one()
                self._copy_locked(entry_id)
        else:
            self._copy_locked(entry_id)

    def _copy_locked(self, entry_id: int):
        for sequence in range(1, 10001):
            entry = self._entry(entry_id)
            if entry.status in {"copied", "applied"}:
                source = _read_verified(self.storage, _storage_path(entry.old_storage_path), entry.checksum_sha256)
                _read_verified(self.storage, entry.new_storage_path, sha256(source).hexdigest())
                return
            data = _read_verified(self.storage, _storage_path(entry.old_storage_path), entry.checksum_sha256)
            source_digest = sha256(data).hexdigest()
            if self.storage.exists(entry.staging_storage_path):
                _read_verified(self.storage, entry.staging_storage_path, source_digest)
            else:
                if entry.status == "moving":
                    if self.storage.exists(entry.new_storage_path):
                        _read_verified(self.storage, entry.new_storage_path, source_digest)
                        self._set_status(entry_id, "copied")
                        return
                    raise RenameError("Staged copy disappeared before move completed")
                if not self.storage.write_bytes_if_absent(entry.staging_storage_path, data, "application/pdf"):
                    _read_verified(self.storage, entry.staging_storage_path, source_digest)
            self._set_status(entry_id, "moving")
            if self.storage.move_if_absent(entry.staging_storage_path, entry.new_storage_path):
                _read_verified(self.storage, entry.new_storage_path, source_digest)
                self._set_status(entry_id, "copied")
                return
            self._set_status(entry_id, "staged", sequence=sequence + 1)
        raise RenameError("No collision-free PDF filename available")

    def _finalize(self, entry_id: int):
        with self.session_factory.begin() as db:
            entry = db.get(UploadedDocumentRename, entry_id)
            file, submission = _locked_file_and_submission(db, entry.submission_file_id)
            entries = self._group_entries(db, entry, lock=True)
            if entries[0].status == "applied":
                return
            if entries[0].status != "copied":
                raise RenameError("PDF copy is not verified")
            if _plan_for_file(db, submission, file) is None:
                raise RenameError("Submission no longer refers to this document", "METADATA_CHANGED")
            group = _assert_unambiguous(db, file, lock=True)
            if {member.id for member in group} != {item.submission_file_id for item in entries}:
                raise RenameError("Document references changed during migration", "AMBIGUOUS_REFERENCE")
            if any((db.get(SubmissionFile, item.submission_file_id).filename,
                    db.get(SubmissionFile, item.submission_file_id).storage_path) !=
                   (item.old_filename, item.old_storage_path) for item in entries):
                raise RenameError("Document metadata changed during migration", "METADATA_CHANGED")
            _read_verified(self.storage, _storage_path(entry.old_storage_path), entry.checksum_sha256)
            _read_verified(self.storage, entry.new_storage_path, entry.checksum_sha256)
            _assert_reference_names_unambiguous(db, submission, group)
            old_filename = PurePosixPath(_storage_path(entry.old_storage_path)).name
            for old_path in {item.old_storage_path for item in entries}:
                _replace_submission_references(submission, old_filename, entry.new_filename,
                                               old_path, entry.new_storage_path)
            for member, journal in ((db.get(SubmissionFile, item.submission_file_id), item) for item in entries):
                member.filename = entry.new_filename
                member.storage_path = entry.new_storage_path
                journal.status = "applied"
                _audit(db, submission, journal, "UPLOADED_DOCUMENT_RENAMED")

    def apply(self, migration_id: str) -> dict:
        self._require_journal()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", migration_id):
            raise RenameError("Invalid migration ID")
        report = {"action": "apply", "migration_id": migration_id, "applied": 0,
                  "skipped": 0, "errors": [], "submissions_checked": 0, "unresolved": []}
        seen_entries = set()
        for submission_id, file_id, _, _ in self._planned_files(report):
            try:
                entry_id = self._prepare(file_id, migration_id)
                if entry_id is None:
                    report["skipped"] += 1
                    continue
                if entry_id in seen_entries:
                    continue
                seen_entries.add(entry_id)
                before = self._entry(entry_id).status
                self._copy(entry_id)
                self._finalize(entry_id)
                report["applied" if before != "applied" else "skipped"] += 1
            except Exception as exc:
                report["errors"].append({"submission_id": submission_id, "file_id": file_id, **_diagnostic(exc)})
        return report

    def verify(self, migration_id: str | None = None) -> dict:
        self._require_journal()
        report = {"action": "verify", "migration_id": migration_id, "verified": 0, "errors": []}
        with self.session_factory() as db:
            query = select(UploadedDocumentRename.id).order_by(UploadedDocumentRename.id)
            if migration_id:
                query = query.where(UploadedDocumentRename.migration_id == migration_id)
            ids = list(db.execute(query).scalars())
        if migration_id and not ids:
            raise RenameError("No entries for migration ID")
        seen_groups = set()
        for entry_id in ids:
            try:
                with self.session_factory() as db:
                    entry = db.get(UploadedDocumentRename, entry_id)
                    key = (entry.migration_id, entry.group_id)
                    if key in seen_groups:
                        continue
                    seen_groups.add(key)
                    entries = self._group_entries(db, entry)
                    file = db.get(SubmissionFile, entry.submission_file_id)
                    if entry.status not in {"applied", "rolled_back"}:
                        raise RenameError("Migration entry is incomplete")
                    group = _assert_unambiguous(db, file)
                    if {member.id for member in group} != {item.submission_file_id for item in entries}:
                        raise RenameError("Document journal group is incomplete", "INCOMPLETE_JOURNAL")
                    if any((db.get(SubmissionFile, item.submission_file_id).filename,
                            db.get(SubmissionFile, item.submission_file_id).storage_path) !=
                           ((item.new_filename, item.new_storage_path) if item.status == "applied" else
                            (item.old_filename, item.old_storage_path)) for item in entries):
                        raise RenameError("Document metadata does not match journal", "METADATA_CHANGED")
                    submission = _submission(db, file)
                    stale = {(PurePosixPath(_storage_path(item.old_storage_path)).name, item.old_storage_path)
                             for item in entries} if entry.status == "applied" else {
                        (entry.new_filename, entry.new_storage_path)}
                    if any(_has_reference(submission, name, path) for name, path in stale):
                        raise RenameError("Submission still refers to prior PDF filename")
                    _read_verified(self.storage, _storage_path(entry.old_storage_path), entry.checksum_sha256)
                    if entry.status == "applied" or self.storage.exists(entry.new_storage_path):
                        _read_verified(self.storage, entry.new_storage_path, entry.checksum_sha256)
                    report["verified"] += 1
            except Exception as exc:
                report["errors"].append({"journal_id": entry_id, **_diagnostic(exc)})
        return report

    def rollback(self, migration_id: str) -> dict:
        self._require_journal()
        report = {"action": "rollback", "migration_id": migration_id, "rolled_back": 0,
                  "skipped": 0, "errors": []}
        with self.session_factory() as db:
            ids = list(db.execute(select(UploadedDocumentRename.id).where(
                UploadedDocumentRename.migration_id == migration_id,
            ).order_by(UploadedDocumentRename.id.desc())).scalars())
        if not ids:
            raise RenameError("No entries for migration ID")
        seen_groups = set()
        for entry_id in ids:
            try:
                with self.session_factory.begin() as db:
                    entry = db.get(UploadedDocumentRename, entry_id)
                    key = (entry.migration_id, entry.group_id)
                    if key in seen_groups:
                        continue
                    seen_groups.add(key)
                    file, submission = _locked_file_and_submission(db, entry.submission_file_id)
                    entries = self._group_entries(db, entry, lock=True)
                    if entry.status == "rolled_back":
                        report["skipped"] += 1
                        continue
                    group = _assert_unambiguous(db, file, lock=True)
                    if {member.id for member in group} != {item.submission_file_id for item in entries}:
                        raise RenameError("Document journal group is incomplete", "INCOMPLETE_JOURNAL")
                    _read_verified(self.storage, _storage_path(entry.old_storage_path), entry.checksum_sha256)
                    if entry.status == "applied":
                        if any((member.filename, member.storage_path) !=
                               (entry.new_filename, entry.new_storage_path) for member in group):
                            raise RenameError("Document metadata changed after migration", "METADATA_CHANGED")
                        old_filename = PurePosixPath(_storage_path(entry.old_storage_path)).name
                        for old_path in sorted({item.old_storage_path for item in entries}):
                            _replace_submission_references(submission, entry.new_filename, old_filename,
                                                           entry.new_storage_path, old_path)
                        for journal in entries:
                            member = db.get(SubmissionFile, journal.submission_file_id)
                            member.filename = journal.old_filename
                            member.storage_path = journal.old_storage_path
                            _audit(db, submission, journal, "UPLOADED_DOCUMENT_RENAME_ROLLED_BACK")
                    elif any((db.get(SubmissionFile, item.submission_file_id).filename,
                               db.get(SubmissionFile, item.submission_file_id).storage_path) !=
                             (item.old_filename, item.old_storage_path) for item in entries):
                        raise RenameError("Document metadata changed during migration", "METADATA_CHANGED")
                    for journal in entries:
                        journal.status = "rolled_back"
                    report["rolled_back"] += 1
            except Exception as exc:
                report["errors"].append({"journal_id": entry_id, **_diagnostic(exc)})
        return report


@contextmanager
def _deployment_lock(engine):
    """One deployment migration at a time per database (session-level lock)."""
    name = "uploaded-document-names:" + sha256(str(engine.url.database).encode()).hexdigest()[:32]
    key = int.from_bytes(sha256(name.encode()).digest()[:8], "big", signed=True)
    with engine.connect() as connection:
        if engine.dialect.name in {"mysql", "mariadb"}:
            acquire = text("SELECT GET_LOCK(:key, 0)")
            release = text("SELECT RELEASE_LOCK(:key)")
            params = {"key": name}
        elif engine.dialect.name == "postgresql":
            acquire = text("SELECT pg_try_advisory_lock(:key)")
            release = text("SELECT pg_advisory_unlock(:key)")
            params = {"key": key}
        else:
            raise RenameError("Automatic mode requires MariaDB/MySQL or PostgreSQL", "UNSUPPORTED_DATABASE")
        if connection.execute(acquire, params).scalar_one() != 1:
            raise RenameError("Another deployment migration holds the lock", "MIGRATION_LOCKED")
        try:
            connection.commit()
            yield
        finally:
            try:
                connection.execute(release, params)
                connection.commit()
            except Exception:
                connection.invalidate()
                raise


def _automatic_migration(renamer):
    renamer._require_journal()
    engine = renamer.session_factory.kw["bind"]
    if getattr(renamer.storage, "verify_ssl", None) is False:
        raise RenameError("Automatic migration requires verified TLS", "TLS_VERIFICATION_DISABLED")
    with _deployment_lock(engine), warnings.catch_warnings():
        # Fail closed instead of silently copying private PDFs over unverified TLS.
        warnings.simplefilter("error", InsecureRequestWarning)
        with renamer.session_factory() as db:
            incomplete = db.execute(select(UploadedDocumentRename.id).where(
                UploadedDocumentRename.migration_id != AUTO_MIGRATION_ID,
                UploadedDocumentRename.status.in_({"planned", "staged", "moving", "copied"}),
            ).limit(1)).scalar_one_or_none()
            rolled_back = db.execute(select(UploadedDocumentRename.id).where(
                UploadedDocumentRename.migration_id == AUTO_MIGRATION_ID,
                UploadedDocumentRename.status == "rolled_back",
            ).limit(1)).scalar_one_or_none()
        if incomplete is not None:
            raise RenameError("An earlier migration needs recovery before automatic deployment", "INCOMPLETE_JOURNAL")
        if rolled_back is not None:
            raise RenameError("Automatic migration was rolled back; review before reapplying", "ROLLED_BACK")
        preview = renamer.dry_run()
        if preview["errors"]:
            return {"action": "auto", "phase": "dry-run", "preview": preview, "errors": preview["errors"]}
        result = renamer.apply(AUTO_MIGRATION_ID)
        if result["errors"]:
            return {"action": "auto", "phase": "apply", "result": result, "errors": result["errors"]}
        with renamer.session_factory() as db:
            exists = db.execute(select(UploadedDocumentRename.id).where(
                UploadedDocumentRename.migration_id == AUTO_MIGRATION_ID,
            ).limit(1)).scalar_one_or_none()
        verification = renamer.verify(AUTO_MIGRATION_ID) if exists is not None else {"verified": 0, "errors": []}
        return {"action": "auto", "phase": "verify", "result": result,
                "verification": verification, "errors": verification["errors"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--dry-run", action="store_true", help="Preview changes (default)")
    action.add_argument("--auto", action="store_true", help="Run deployment migration under a database lock")
    action.add_argument("--apply", action="store_true", help="Copy, verify, and switch references")
    action.add_argument("--verify", action="store_true", help="Check stored copies and metadata")
    action.add_argument("--rollback", action="store_true", help="Restore references for one migration ID")
    parser.add_argument("--migration-id", help="ID printed by --apply; required for rollback")
    args = parser.parse_args(argv)
    if args.auto and args.migration_id:
        parser.error("--auto uses its own stable migration ID")
    if args.rollback and not args.migration_id:
        parser.error("--rollback requires --migration-id")
    if not Config.DATABASE_URL:
        parser.error("DATABASE_URL is required")
    storage = create_nextcloud_storage_from_env()
    renamer = UploadedDocumentRenamer(create_session_factory(Config.DATABASE_URL), storage)
    try:
        if args.auto:
            result = _automatic_migration(renamer)
        elif args.apply:
            migration_id = args.migration_id or uuid4().hex
            print(json.dumps({"migration_id": migration_id}, ensure_ascii=False), flush=True)
            result = renamer.apply(migration_id)
        elif args.verify:
            result = renamer.verify(args.migration_id)
        elif args.rollback:
            result = renamer.rollback(args.migration_id)
        else:
            result = renamer.dry_run()
    except Exception as exc:
        message = str(exc) if isinstance(exc, RenameError) else "Operation failed"
        print(json.dumps({"error": type(exc).__name__, "message": message}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
