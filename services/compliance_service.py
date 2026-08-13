from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import unicodedata

from sqlalchemy import select

from models import (
    ConsentDefinition,
    ConsentVersion,
    Form,
    FormRegulationVersion,
    FormSubmission,
    FormVersion,
    FormVersionConsent,
    SubmissionConsent,
)


CONSENT_TYPES = {
    "regulation",
    "rodo",
    "data_processing",
    "participation_obligation",
    "participant_statement",
    "other",
}


class ComplianceError(ValueError):
    pass


def normalize_consent_text(value: object) -> str:
    text = unicodedata.normalize("NFC", str(value or "")).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def consent_sha256(value: object) -> str:
    return sha256(normalize_consent_text(value).encode("utf-8")).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_accepted(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        return any(_is_accepted(item) for item in value)
    return str(value or "").strip().casefold() in {"1", "true", "tak", "yes", "on", "checked"}


def _consent_fields(definition: dict) -> list[dict]:
    return [
        field
        for field in definition.get("fields") or []
        if isinstance(field, dict) and field.get("type") == "checkbox" and field.get("name")
    ]


def _consent_text(field: dict) -> str:
    options = field.get("options") or []
    first = options[0] if isinstance(options, list) and options else {}
    option_label = first.get("label", "") if isinstance(first, dict) else str(first or "")
    return normalize_consent_text(field.get("pdf_text") or option_label or field.get("label") or "")


class ComplianceService:
    """Freezes consent/regulation versions and records immutable acceptance evidence."""

    def __init__(self, submission_repository=None) -> None:
        self.submission_repository = submission_repository

    def validate_acceptances(self, form_config: dict, submission_data: dict) -> dict[str, str]:
        errors: dict[str, str] = {}
        for field in _consent_fields(form_config):
            key = str(field["name"])
            if field.get("required") and not _is_accepted(submission_data.get(key)):
                errors[key] = f"Zgoda „{field.get('label') or key}” jest wymagana."
        return errors

    def build_unversioned_snapshots(self, form_config: dict, submission_data: dict) -> list[dict]:
        now = datetime.now(timezone.utc).isoformat()
        snapshots = []
        for field in _consent_fields(form_config):
            text = _consent_text(field)
            accepted = _is_accepted(submission_data.get(str(field["name"])))
            snapshots.append(
                {
                    "consent_key": str(field["name"]),
                    "consent_version": "storage-snapshot",
                    "consent_title_snapshot": str(field.get("label") or field["name"]),
                    "consent_text_snapshot": text,
                    "consent_sha256": consent_sha256(text),
                    "accepted": accepted,
                    "accepted_at": now if accepted else None,
                }
            )
        return snapshots

    def stage_form_version(self, db, form: Form, form_version: FormVersion, *, actor_id: int | None) -> None:
        if form_version.status != "draft":
            raise ComplianceError("Szkice compliance można tworzyć wyłącznie dla roboczej wersji formularza.")
        selected_ids: set[int] = set()
        for field in _consent_fields(form_version.definition_json or {}):
            key = str(field["name"])
            consent_type = str(field.get("consent_type") or "other").strip() or "other"
            if consent_type not in CONSENT_TYPES:
                raise ComplianceError(f"Nieobsługiwany typ zgody „{consent_type}” dla pola „{key}”.")
            definition = db.execute(select(ConsentDefinition).where(ConsentDefinition.form_id == form.id, ConsentDefinition.consent_key == key)).scalar_one_or_none()
            if not definition:
                definition = ConsentDefinition(form_id=form.id, consent_key=key, consent_type=consent_type)
                db.add(definition)
                db.flush()
            definition.consent_type = consent_type
            selected_ids.add(definition.id)
            text = _consent_text(field)
            digest = consent_sha256(text)
            title = str(field.get("label") or key)
            required = bool(field.get("required"))
            published = db.execute(select(ConsentVersion).where(ConsentVersion.consent_definition_id == definition.id, ConsentVersion.status == "published")).scalars().first()
            draft = db.execute(select(ConsentVersion).where(ConsentVersion.consent_definition_id == definition.id, ConsentVersion.status == "draft")).scalars().first()
            if published and (published.sha256, published.title, published.required, published.consent_type) == (digest, title, required, consent_type):
                if draft:
                    db.delete(draft)
                continue
            if not draft:
                latest = db.execute(select(ConsentVersion).where(ConsentVersion.consent_definition_id == definition.id).order_by(ConsentVersion.version_major.desc(), ConsentVersion.version_minor.desc())).scalars().first()
                major = latest.version_major if latest else 1
                minor = latest.version_minor + 1 if latest else 0
                draft = ConsentVersion(consent_definition_id=definition.id, version_major=major, version_minor=minor, version_label=f"{major}.{minor}", status="draft", created_by_id=actor_id)
                db.add(draft)
            draft.consent_type = consent_type
            draft.title = title
            draft.text_snapshot = text
            draft.sha256 = digest
            draft.required = required
        stale_drafts = db.execute(select(ConsentVersion).join(ConsentDefinition).where(ConsentDefinition.form_id == form.id, ConsentVersion.status == "draft")).scalars().all()
        for draft in stale_drafts:
            if draft.consent_definition_id not in selected_ids:
                db.delete(draft)
        db.flush()

    def stage_regulation_version(self, db, form: Form, regulation=None, *, actor_id: int | None) -> FormRegulationVersion:
        regulation = regulation or form.regulation
        if not regulation or not regulation.storage_path or not Path(regulation.storage_path).is_file():
            raise ComplianceError("Nie można utworzyć szkicu regulaminu bez poprawnego pliku.")
        digest = file_sha256(regulation.storage_path)
        draft = db.execute(select(FormRegulationVersion).where(FormRegulationVersion.regulation_id == regulation.id, FormRegulationVersion.status == "draft")).scalars().first()
        if not draft:
            latest = db.execute(select(FormRegulationVersion).where(FormRegulationVersion.regulation_id == regulation.id).order_by(FormRegulationVersion.version_major.desc(), FormRegulationVersion.version_minor.desc())).scalars().first()
            major = latest.version_major if latest else 1
            minor = latest.version_minor + 1 if latest else 0
            draft = FormRegulationVersion(regulation_id=regulation.id, form_id=form.id, version_major=major, version_minor=minor, version_label=f"{major}.{minor}", status="draft", created_by_id=actor_id)
            db.add(draft)
        draft.title = f"Regulamin — {form.name}"
        draft.original_filename = regulation.original_filename
        draft.storage_path = regulation.storage_path
        draft.mime_type = regulation.mime_type
        draft.size_bytes = regulation.size_bytes
        draft.sha256 = digest
        db.flush()
        return draft

    def freeze_form_version(
        self,
        db,
        form: Form,
        form_version: FormVersion,
        *,
        actor_id: int | None,
    ) -> None:
        if form_version.form_id != form.id:
            raise ComplianceError("Wersja formularza nie należy do formularza.")
        if form_version.consent_links or form_version.regulation_version_id:
            return

        now = datetime.now(timezone.utc)
        selected_definition_ids: set[int] = set()
        for sort_order, field in enumerate(_consent_fields(form_version.definition_json or {})):
            key = str(field["name"])
            consent_type = str(field.get("consent_type") or "other").strip() or "other"
            if consent_type not in CONSENT_TYPES:
                raise ComplianceError(f"Nieobsługiwany typ zgody „{consent_type}” dla pola „{key}”.")
            definition = db.execute(
                select(ConsentDefinition).where(
                    ConsentDefinition.form_id == form.id,
                    ConsentDefinition.consent_key == key,
                )
            ).scalar_one_or_none()
            if not definition:
                definition = ConsentDefinition(form_id=form.id, consent_key=key, consent_type=consent_type)
                db.add(definition)
                db.flush()
            elif definition.consent_type != consent_type:
                definition.consent_type = consent_type
            selected_definition_ids.add(definition.id)

            text = _consent_text(field)
            digest = consent_sha256(text)
            title = str(field.get("label") or key)
            required = bool(field.get("required"))
            current = db.execute(
                select(ConsentVersion)
                .where(ConsentVersion.consent_definition_id == definition.id, ConsentVersion.status == "published")
                .order_by(ConsentVersion.version_major.desc(), ConsentVersion.version_minor.desc())
            ).scalars().first()
            if current and current.sha256 == digest and current.title == title and current.required == required and current.consent_type == consent_type:
                consent_version = current
            else:
                if current:
                    current.status = "archived"
                    current.archived_at = now
                staged = db.execute(
                    select(ConsentVersion).where(
                        ConsentVersion.consent_definition_id == definition.id,
                        ConsentVersion.status == "draft",
                        ConsentVersion.sha256 == digest,
                    )
                ).scalars().first()
                latest = db.execute(
                    select(ConsentVersion)
                    .where(ConsentVersion.consent_definition_id == definition.id)
                    .order_by(ConsentVersion.version_major.desc(), ConsentVersion.version_minor.desc())
                ).scalars().first()
                major = latest.version_major if latest else 1
                minor = latest.version_minor + 1 if latest else 0
                consent_version = staged or ConsentVersion(
                    consent_definition_id=definition.id,
                    version_major=major,
                    version_minor=minor,
                    version_label=f"{major}.{minor}",
                    consent_type=consent_type,
                    title=title,
                    text_snapshot=text,
                    sha256=digest,
                    required=required,
                    status="draft",
                    created_by_id=actor_id,
                )
                if not staged:
                    db.add(consent_version)
                consent_version.status = "published"
                consent_version.published_at = now
                db.flush()
            form_version.consent_links.append(
                FormVersionConsent(consent_version_id=consent_version.id, sort_order=sort_order)
            )

        published_for_removed = db.execute(
            select(ConsentVersion)
            .join(ConsentDefinition)
            .where(ConsentDefinition.form_id == form.id, ConsentVersion.status == "published")
        ).scalars().all()
        for item in published_for_removed:
            if item.consent_definition_id not in selected_definition_ids:
                item.status = "archived"
                item.archived_at = now

        regulation = form.regulation
        if regulation and regulation.storage_path:
            path = Path(regulation.storage_path)
            if not path.is_file():
                raise ComplianceError("Nie można opublikować formularza: plik regulaminu nie istnieje.")
            digest = file_sha256(path)
            current_regulation = db.execute(
                select(FormRegulationVersion)
                .where(FormRegulationVersion.regulation_id == regulation.id, FormRegulationVersion.status == "published")
                .order_by(FormRegulationVersion.version_major.desc(), FormRegulationVersion.version_minor.desc())
            ).scalars().first()
            if current_regulation and current_regulation.sha256 == digest:
                regulation_version = current_regulation
            else:
                if current_regulation:
                    current_regulation.status = "archived"
                    current_regulation.archived_at = now
                staged = db.execute(
                    select(FormRegulationVersion).where(
                        FormRegulationVersion.regulation_id == regulation.id,
                        FormRegulationVersion.status == "draft",
                        FormRegulationVersion.sha256 == digest,
                    )
                ).scalars().first()
                latest = db.execute(
                    select(FormRegulationVersion)
                    .where(FormRegulationVersion.regulation_id == regulation.id)
                    .order_by(FormRegulationVersion.version_major.desc(), FormRegulationVersion.version_minor.desc())
                ).scalars().first()
                major = latest.version_major if latest else 1
                minor = latest.version_minor + 1 if latest else 0
                regulation_version = staged or FormRegulationVersion(
                    regulation_id=regulation.id,
                    form_id=form.id,
                    version_major=major,
                    version_minor=minor,
                    version_label=f"{major}.{minor}",
                    title=f"Regulamin — {form.name}",
                    original_filename=regulation.original_filename,
                    storage_path=regulation.storage_path,
                    mime_type=regulation.mime_type,
                    size_bytes=regulation.size_bytes,
                    sha256=digest,
                    status="draft",
                    created_by_id=actor_id,
                )
                if not staged:
                    db.add(regulation_version)
                regulation_version.status = "published"
                regulation_version.published_at = now
                db.flush()
            form_version.regulation_version_id = regulation_version.id
        db.flush()

    def record_submission_acceptances(
        self,
        *,
        submission_id: str,
        form_version_id: int | None,
        submission_data: dict,
    ) -> list[SubmissionConsent]:
        session_factory = getattr(self.submission_repository, "session_factory", None)
        if not session_factory or not form_version_id:
            return []
        now = datetime.now(timezone.utc)
        with session_factory() as db:
            submission = db.execute(
                select(FormSubmission).where(FormSubmission.submission_id == submission_id)
            ).scalar_one()
            form_version = db.get(FormVersion, form_version_id)
            if not form_version or form_version.form_id != submission.form_version.form_id:
                raise ComplianceError("Nie można rozwiązać wersji compliance dla zgłoszenia.")
            if submission.consents:
                return list(submission.consents)
            records = []
            for link in form_version.consent_links:
                version = link.consent_version
                definition = version.definition
                accepted = _is_accepted(submission_data.get(definition.consent_key))
                if version.required and not accepted:
                    raise ComplianceError(f"Wymagana zgoda „{version.title}” nie została zaakceptowana.")
                regulation_version = (
                    form_version.regulation_version
                    if version.consent_type == "regulation" or "regulamin" in definition.consent_key.casefold()
                    else None
                )
                record = SubmissionConsent(
                    submission_id=submission.id,
                    consent_version_id=version.id,
                    regulation_version_id=regulation_version.id if regulation_version else None,
                    consent_key=definition.consent_key,
                    consent_version=version.version_label,
                    consent_title_snapshot=version.title,
                    consent_text_snapshot=version.text_snapshot,
                    consent_sha256=version.sha256,
                    regulation_version=regulation_version.version_label if regulation_version else "",
                    regulation_sha256=regulation_version.sha256 if regulation_version else "",
                    accepted=accepted,
                    accepted_at=now if accepted else None,
                    metadata_json={"consent_type": version.consent_type},
                )
                db.add(record)
                records.append(record)
            db.commit()
            for record in records:
                db.refresh(record)
            return records

    def remove_incomplete_submission(self, submission_id: str) -> None:
        session_factory = getattr(self.submission_repository, "session_factory", None)
        if not session_factory:
            return
        with session_factory() as db:
            submission = db.execute(
                select(FormSubmission).where(FormSubmission.submission_id == submission_id)
            ).scalar_one_or_none()
            if submission:
                db.delete(submission)
                db.commit()

    @staticmethod
    def audit_description(record: SubmissionConsent) -> str:
        accepted_at = record.accepted_at.isoformat() if record.accepted_at else "brak daty (migracja legacy)"
        version = record.regulation_version or record.consent_version
        digest = record.regulation_sha256 or record.consent_sha256
        return f"Użytkownik zaakceptował {record.consent_title_snapshot} v{version} o SHA-256 {digest} dnia {accepted_at}."
