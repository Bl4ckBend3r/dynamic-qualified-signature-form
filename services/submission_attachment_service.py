from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePosixPath
from uuid import uuid4
from typing import Protocol

from form_loader import evaluate_visible_if
from services.upload_validation import UploadValidationError, validate_attachment_upload


ATTACHMENT_DOCUMENT_TYPE = "submission_attachment"
CURRENT_ATTACHMENT_STATUSES = {"active", "valid"}
REPLACEMENT_REQUIRED_STATUSES = {"rejected", "requires_correction"}
ANTIVIRUS_NOT_CONFIGURED = "not_configured"
ANTIVIRUS_STATUSES = {"pending", "clean", "infected", "unavailable", ANTIVIRUS_NOT_CONFIGURED}


class AttachmentAntivirusScanner(Protocol):
    def scan(self, content: bytes, mime_type: str) -> str: ...


class NotConfiguredAntivirusScanner:
    def scan(self, content: bytes, mime_type: str) -> str:
        return ANTIVIRUS_NOT_CONFIGURED


@dataclass(frozen=True)
class PreparedAttachment:
    field_key: str
    original_filename: str
    extension: str
    mime_type: str
    content: bytes
    checksum_sha256: str
    document_type: str
    category: str
    antivirus_status: str


class SubmissionAttachmentService:
    """Validates and versions participant uploads through the existing storage layer."""

    def __init__(self, submission_repository, storage, antivirus_scanner: AttachmentAntivirusScanner | None = None) -> None:
        self.submission_repository = submission_repository
        self.storage = storage
        self.antivirus_scanner = antivirus_scanner or NotConfiguredAntivirusScanner()

    def validate_uploads(
        self,
        form_config: dict,
        submission_data: dict,
        uploaded_files,
        *,
        submission_id: str | None = None,
    ) -> tuple[list[PreparedAttachment], dict[str, str]]:
        prepared: list[PreparedAttachment] = []
        errors: dict[str, str] = {}
        for field in form_config.get("fields") or []:
            if field.get("type") not in {"file", "attachment"}:
                continue
            if field.get("readonly") or field.get("hidden"):
                continue
            key = str(field.get("name") or "").strip()
            if not key:
                continue
            if not evaluate_visible_if(field.get("visible_if"), submission_data):
                continue
            files = [item for item in self._getlist(uploaded_files, key) if getattr(item, "filename", "")]
            required = bool(field.get("required"))
            required_if = field.get("required_if")
            if required_if:
                required = evaluate_visible_if(required_if, submission_data)
            existing_current, replacement_required = self._existing_state(submission_id, key)
            if replacement_required and not files:
                errors[key] = f"Załącznik „{field.get('label') or key}” wymaga wymiany."
                continue
            if required and not files and not existing_current:
                errors[key] = f"Pole „{field.get('label') or key}” jest wymagane."
                continue
            max_files = int(field.get("max_files", 1) or 1)
            if len(files) > max_files:
                errors[key] = f"Można przesłać maksymalnie {max_files} plików."
                continue
            for uploaded in files:
                content = uploaded.read()
                try:
                    original, detected_mime = validate_attachment_upload(
                        uploaded.filename,
                        content,
                        getattr(uploaded, "mimetype", ""),
                        allowed_extensions=field.get("allowed_extensions") or ["pdf"],
                        allowed_mime_types=field.get("allowed_mime_types") or [],
                        max_size_bytes=int(float(field.get("max_size_mb", 10) or 10) * 1024 * 1024),
                    )
                except (UploadValidationError, TypeError, ValueError) as exc:
                    errors[key] = str(exc)
                    break
                antivirus_status = str(self.antivirus_scanner.scan(content, detected_mime) or "unavailable")
                if antivirus_status not in ANTIVIRUS_STATUSES:
                    antivirus_status = "unavailable"
                if antivirus_status == "infected":
                    errors[key] = "Plik został odrzucony przez skaner bezpieczeństwa."
                    break
                prepared.append(
                    PreparedAttachment(
                        field_key=key,
                        original_filename=original,
                        extension=PurePosixPath(original).suffix.lower(),
                        mime_type=detected_mime,
                        content=content,
                        checksum_sha256=sha256(content).hexdigest(),
                        document_type=str(field.get("document_type") or ATTACHMENT_DOCUMENT_TYPE),
                        category=str(field.get("category") or ""),
                        antivirus_status=antivirus_status,
                    )
                )
        if errors:
            return [], errors
        return prepared, {}

    def persist(
        self,
        *,
        form_slug: str,
        submission_id: str,
        attachments: list[PreparedAttachment],
        workflow_step: str = "",
        source: str = "participant",
    ) -> list[dict]:
        if not attachments:
            return []
        if not hasattr(self.submission_repository, "session_factory"):
            return self._persist_legacy(form_slug, submission_id, attachments, workflow_step, source)

        from sqlalchemy import func, select
        from models import Form, FormField, FormSubmission, SubmissionFile

        written_paths: list[str] = []
        result: list[dict] = []
        try:
            with self.submission_repository.session_factory() as db:
                submission = db.execute(
                    select(FormSubmission).where(FormSubmission.submission_id == submission_id)
                ).scalar_one()
                form = db.execute(select(Form).where(Form.slug == form_slug)).scalar_one_or_none()
                field_classifications = {
                    field.name: field.data_classification
                    for field in db.execute(select(FormField).where(FormField.form_id == form.id)).scalars()
                } if form else {}
                by_field: dict[str, list[PreparedAttachment]] = {}
                for item in attachments:
                    by_field.setdefault(item.field_key, []).append(item)
                for field_key, items in by_field.items():
                    current = db.execute(
                        select(SubmissionFile).where(
                            SubmissionFile.submission_id == submission.id,
                            SubmissionFile.field_key == field_key,
                            SubmissionFile.status.in_(CURRENT_ATTACHMENT_STATUSES | REPLACEMENT_REQUIRED_STATUSES),
                        )
                    ).scalars().all()
                    for old in current:
                        old.status = "superseded"
                    version = db.execute(
                        select(func.max(SubmissionFile.attachment_version)).where(
                            SubmissionFile.submission_id == submission.id,
                            SubmissionFile.field_key == field_key,
                        )
                    ).scalar_one_or_none() or 0
                    for item in items:
                        version += 1
                        filename = f"{uuid4().hex}{item.extension}"
                        path = self._storage_path(form_slug, submission_id, field_key, filename)
                        self._ensure_parent(path)
                        self.storage.write_bytes(path, item.content, item.mime_type)
                        written_paths.append(path)
                        if sha256(self.storage.read_bytes(path)).hexdigest() != item.checksum_sha256:
                            raise IOError("Weryfikacja SHA-256 zapisanego załącznika nie powiodła się.")
                        row = SubmissionFile(
                            submission_id=submission.id,
                            public_submission_id=submission.submission_id,
                            form_slug=form_slug,
                            document_id=field_key,
                            document_type=item.document_type,
                            file_role="participant_attachment",
                            storage_provider=self.storage.__class__.__name__,
                            filename=filename,
                            original_filename=item.original_filename,
                            storage_path=path,
                            mime_type=item.mime_type,
                            size_bytes=len(item.content),
                            checksum_sha256=item.checksum_sha256,
                            status="active",
                            field_key=field_key,
                            attachment_version=version,
                            category=item.category,
                            data_classification=field_classifications.get(field_key, "normal"),
                            uploaded_by_source=source,
                            workflow_step_at_upload=workflow_step,
                            antivirus_status=item.antivirus_status,
                        )
                        db.add(row)
                        db.flush()
                        result.append({"id": row.id, "storage_path": path, "version": version})
                db.commit()
            return result
        except Exception:
            for path in written_paths:
                try:
                    self.storage.delete(path, missing_ok=True)
                except Exception:
                    pass
            raise

    def _persist_legacy(self, form_slug, submission_id, attachments, workflow_step, source):
        existing = self.submission_repository.get_by_id(submission_id) or {}
        data_json = dict(existing.get("data_json") or {})
        history = [dict(item) for item in data_json.get("_attachments", []) if isinstance(item, dict)]
        replaced_fields = {item.field_key for item in attachments}
        for old in history:
            if old.get("field_key") in replaced_fields and old.get("status") in CURRENT_ATTACHMENT_STATUSES | REPLACEMENT_REQUIRED_STATUSES:
                old["status"] = "superseded"
        result = []
        for item in attachments:
            version = max(
                (int(old.get("attachment_version") or 0) for old in history if old.get("field_key") == item.field_key),
                default=0,
            ) + 1
            filename = f"{uuid4().hex}{item.extension}"
            path = self._storage_path(form_slug, submission_id, item.field_key, filename)
            self._ensure_parent(path)
            self.storage.write_bytes(path, item.content, item.mime_type)
            if sha256(self.storage.read_bytes(path)).hexdigest() != item.checksum_sha256:
                self.storage.delete(path, missing_ok=True)
                raise IOError("Weryfikacja SHA-256 zapisanego załącznika nie powiodła się.")
            metadata = {
                "filename": filename, "original_filename": item.original_filename,
                "storage_path": path, "mime_type": item.mime_type, "size_bytes": len(item.content),
                "checksum_sha256": item.checksum_sha256, "document_id": item.field_key,
                "document_type": item.document_type, "status": "active", "field_key": item.field_key,
                "attachment_version": version, "category": item.category,
                "uploaded_by_source": source, "workflow_step_at_upload": workflow_step,
                "antivirus_status": item.antivirus_status,
            }
            self.submission_repository.record_file(submission_id, metadata)
            history.append(metadata)
            result.append(metadata)
        data_json["_attachments"] = history
        self.submission_repository.update(submission_id, {"data_json": data_json})
        return result

    def _existing_state(self, submission_id: str | None, field_key: str) -> tuple[bool, bool]:
        if not submission_id or not hasattr(self.submission_repository, "session_factory"):
            return False, False
        from sqlalchemy import select
        from models import SubmissionFile
        with self.submission_repository.session_factory() as db:
            statuses = db.execute(
                select(SubmissionFile.status).where(
                    SubmissionFile.public_submission_id == submission_id,
                    SubmissionFile.field_key == field_key,
                )
            ).scalars().all()
        return any(item in CURRENT_ATTACHMENT_STATUSES for item in statuses), any(item in REPLACEMENT_REQUIRED_STATUSES for item in statuses)

    @staticmethod
    def _getlist(uploaded_files, key):
        if uploaded_files is None:
            return []
        if hasattr(uploaded_files, "getlist"):
            return uploaded_files.getlist(key)
        value = uploaded_files.get(key, []) if hasattr(uploaded_files, "get") else []
        return value if isinstance(value, list) else [value]

    def _storage_path(self, form_slug, submission_id, field_key, filename):
        parts = [str(item) for item in (form_slug, submission_id, field_key) if item]
        if any(not part.replace("-", "").replace("_", "").isalnum() for part in parts):
            raise ValueError("Nieprawidłowy identyfikator ścieżki załącznika.")
        return "/".join((str(getattr(self.storage, "output_dir", "output")).strip("/"), form_slug, "submissions", submission_id, "attachments", field_key, filename))

    def _ensure_parent(self, path: str) -> None:
        parts = path.split("/")[:-1]
        for index in range(1, len(parts) + 1):
            self.storage.mkdir("/".join(parts[:index]))
