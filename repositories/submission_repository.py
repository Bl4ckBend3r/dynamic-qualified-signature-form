from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from services.form_submission_mapper import (
    FORM_SUBMISSION_COLUMNS,
    build_submission_from_form,
    serialize_value_for_app,
)

logger = logging.getLogger(__name__)


class SubmissionRepository:
    def create(self, submission: dict) -> str:
        raise NotImplementedError

    def get_by_id(self, submission_id: str) -> dict | None:
        raise NotImplementedError

    def update(self, submission_id: str, updates: dict) -> bool:
        raise NotImplementedError

    def list_by_form(self, form_slug: str) -> list[dict]:
        raise NotImplementedError

    def find_by_pdf(self, form_slug: str, filename: str) -> dict | None:
        raise NotImplementedError

    def record_file(self, submission_id: str, metadata: dict) -> bool:
        return False

    def get_file_metadata(self, submission_id: str, filename: str, signed: bool | None = None) -> dict | None:
        return None


class CsvSubmissionRepository(SubmissionRepository):
    """Repository adapter for the current CSV/Nextcloud storage implementation."""

    def __init__(self, storage: Any, form_slugs: list[str] | None = None) -> None:
        self.storage = storage
        self.form_slugs = form_slugs or []

    def create(self, submission: dict) -> str:
        form_slug = str(submission.get("form_slug") or "").strip()
        submission_id = str(submission.get("submission_id") or "").strip()
        if not form_slug:
            raise ValueError("submission.form_slug is required")
        if not submission_id:
            raise ValueError("submission.submission_id is required")
        self.storage.append_csv_row(form_slug, submission)
        if form_slug not in self.form_slugs:
            self.form_slugs.append(form_slug)
        return submission_id

    def get_by_id(self, submission_id: str) -> dict | None:
        wanted = str(submission_id or "").strip()
        if not wanted:
            return None
        for slug in self.form_slugs:
            for row in self.list_by_form(slug):
                if str(row.get("submission_id") or "").strip() == wanted:
                    row = dict(row)
                    row.setdefault("form_slug", slug)
                    return row
        return None

    def update(self, submission_id: str, updates: dict) -> bool:
        existing = self.get_by_id(submission_id)
        if not existing:
            return False
        form_slug = str(existing.get("form_slug") or "").strip()
        return bool(self.storage.update_csv_row_by_submission_id(form_slug, submission_id, updates))

    def list_by_form(self, form_slug: str) -> list[dict]:
        rows = self.storage.read_csv_rows(form_slug)
        normalized = []
        for row in rows:
            row = dict(row)
            row.setdefault("form_slug", form_slug)
            normalized.append(row)
        return normalized

    def find_by_pdf(self, form_slug: str, filename: str) -> dict | None:
        wanted = Path(filename).name
        for row in self.list_by_form(form_slug):
            known_filenames = {
                row.get("pdf_filename", ""),
                row.get("signed_pdf_filename", ""),
                row.get("declaration_filename", ""),
                row.get("declaration_signed_filename", ""),
                row.get("agreement_filename", ""),
                row.get("agreement_signed_filename", ""),
            }
            for agreement in self._parse_json_list(row.get("training_agreements")):
                known_filenames.add(agreement.get("filename", ""))
                known_filenames.add(agreement.get("signed_filename", ""))
            if wanted in known_filenames:
                return row
        return None

    def _parse_json_list(self, value: str | list | None) -> list[dict]:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        raw = str(value or "").strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [item for item in parsed if isinstance(item, dict)]


class PostgresSubmissionRepository(SubmissionRepository):
    """Repository adapter for PostgreSQL-backed form_submissions."""

    def __init__(self, database_url: str, session_factory=None, create_schema: bool = False) -> None:
        if session_factory is None:
            from database import create_engine, create_session_factory

            session_factory = create_session_factory(database_url)
            if create_schema:
                from models import Base

                Base.metadata.create_all(bind=create_engine(database_url))

        self.session_factory = session_factory

    def create(self, submission: dict) -> str:
        from models import FormSubmission

        mapped, meta = build_submission_from_form(submission, include_metadata=True)
        values = self._model_values(mapped)

        with self.session_factory() as session:
            model = FormSubmission(**values)
            session.add(model)
            session.commit()
            session.refresh(model)

        submission["id"] = model.id
        logger.info(
            "Zapisano zgloszenie %s do PostgreSQL. Pola zapisane: %s. Pola pominiete: %s.",
            model.submission_id,
            ", ".join(meta["saved_fields"]),
            ", ".join(meta["skipped_fields"]) or "-",
        )
        return model.submission_id

    def get_by_id(self, submission_id: str) -> dict | None:
        from sqlalchemy import select
        from models import FormSubmission

        wanted = str(submission_id or "").strip()
        if not wanted:
            return None

        with self.session_factory() as session:
            model = session.execute(
                select(FormSubmission).where(FormSubmission.submission_id == wanted)
            ).scalar_one_or_none()
            return self._to_dict(model) if model else None

    def update(self, submission_id: str, updates: dict) -> bool:
        from sqlalchemy import select
        from models import FormSubmission

        wanted = str(submission_id or "").strip()
        if not wanted:
            return False

        mapped, meta = build_submission_from_form(updates, include_metadata=True)
        saved_columns = {
            item.rsplit("->", 1)[1]
            for item in meta["saved_fields"]
            if "->" in item
        }
        values = {
            key: mapped[key]
            for key in saved_columns
            if key in mapped and key in FORM_SUBMISSION_COLUMNS and key != "id"
        }

        with self.session_factory() as session:
            model = session.execute(
                select(FormSubmission).where(FormSubmission.submission_id == wanted)
            ).scalar_one_or_none()
            if not model:
                return False
            for key, value in values.items():
                setattr(model, key, value)
            session.commit()

        logger.info(
            "Zaktualizowano zgloszenie %s w PostgreSQL. Pola zapisane: %s. Pola pominiete: %s.",
            wanted,
            ", ".join(meta["saved_fields"]),
            ", ".join(meta["skipped_fields"]) or "-",
        )
        return True

    def list_by_form(self, form_slug: str) -> list[dict]:
        from sqlalchemy import select
        from models import FormSubmission

        with self.session_factory() as session:
            rows = session.execute(
                select(FormSubmission)
                .where(FormSubmission.form_slug == form_slug)
                .order_by(FormSubmission.created_at.desc())
            ).scalars().all()
            return [self._to_dict(row) for row in rows]

    def find_by_pdf(self, form_slug: str, filename: str) -> dict | None:
        from sqlalchemy import or_, select
        from models import FormSubmission, SubmissionFile

        wanted = Path(filename).name
        with self.session_factory() as session:
            file_row = session.execute(
                select(SubmissionFile)
                .where(SubmissionFile.form_slug == form_slug)
                .where(SubmissionFile.filename == wanted)
            ).scalar_one_or_none()
            if file_row:
                return self.get_by_id(file_row.public_submission_id)

            model = session.execute(
                select(FormSubmission)
                .where(FormSubmission.form_slug == form_slug)
                .where(
                    or_(
                        FormSubmission.pdf_filename == wanted,
                        FormSubmission.signed_pdf_filename == wanted,
                        FormSubmission.declaration_filename == wanted,
                        FormSubmission.declaration_signed_filename == wanted,
                        FormSubmission.agreement_filename == wanted,
                        FormSubmission.agreement_signed_filename == wanted,
                    )
                )
            ).scalar_one_or_none()
            return self._to_dict(model) if model else None

    def record_file(self, submission_id: str, metadata: dict) -> bool:
        from sqlalchemy import select
        from models import FormSubmission, SubmissionFile

        wanted = str(submission_id or "").strip()
        if not wanted:
            return False

        with self.session_factory() as session:
            submission = session.execute(
                select(FormSubmission).where(FormSubmission.submission_id == wanted)
            ).scalar_one_or_none()
            if not submission:
                return False

            existing = session.execute(
                select(SubmissionFile)
                .where(SubmissionFile.submission_id == submission.id)
                .where(SubmissionFile.filename == str(metadata.get("filename") or ""))
                .where(SubmissionFile.document_id == str(metadata.get("document_id") or ""))
                .where(SubmissionFile.signed == bool(metadata.get("signed", False)))
            ).scalar_one_or_none()
            file_row = existing or SubmissionFile(
                submission_id=submission.id,
                public_submission_id=submission.submission_id,
                form_slug=submission.form_slug,
                filename=str(metadata.get("filename") or ""),
                storage_path=str(metadata.get("storage_path") or ""),
            )
            file_row.document_id = str(metadata.get("document_id") or "")
            file_row.document_type = str(metadata.get("document_type") or "")
            file_row.storage_path = str(metadata.get("storage_path") or "")
            file_row.mime_type = str(metadata.get("mime_type") or "application/pdf")
            file_row.size_bytes = metadata.get("size_bytes")
            file_row.signed = bool(metadata.get("signed", False))
            file_row.checksum_sha256 = str(metadata.get("checksum_sha256") or "")
            file_row.status = str(metadata.get("status") or "uploaded")
            if not existing:
                session.add(file_row)
            session.commit()

        logger.info(
            "Zapisano metadane pliku %s dla zgloszenia %s w PostgreSQL.",
            metadata.get("filename"),
            wanted,
        )
        return True

    def get_file_metadata(self, submission_id: str, filename: str, signed: bool | None = None) -> dict | None:
        from sqlalchemy import select
        from models import SubmissionFile

        wanted_submission_id = str(submission_id or "").strip()
        wanted_filename = Path(filename).name
        if not wanted_submission_id or not wanted_filename:
            return None

        with self.session_factory() as session:
            query = (
                select(SubmissionFile)
                .where(SubmissionFile.public_submission_id == wanted_submission_id)
                .where(SubmissionFile.filename == wanted_filename)
            )
            if signed is not None:
                query = query.where(SubmissionFile.signed == bool(signed))
            file_row = session.execute(query).scalar_one_or_none()
            if not file_row:
                return None
            return {
                column.name: getattr(file_row, column.name)
                for column in file_row.__table__.columns
            }

    def _model_values(self, mapped: dict, *, include_defaults: bool = True) -> dict:
        ignored = {"id"}
        if not include_defaults:
            ignored.update(key for key in mapped if key not in FORM_SUBMISSION_COLUMNS)
        return {
            key: value
            for key, value in mapped.items()
            if key in FORM_SUBMISSION_COLUMNS and key not in ignored
        }

    def _to_dict(self, model) -> dict:
        data = {
            column.name: serialize_value_for_app(column.name, getattr(model, column.name))
            for column in model.__table__.columns
        }
        raw_data_json = getattr(model, "data_json", None)
        if isinstance(raw_data_json, dict):
            for key, value in raw_data_json.items():
                data.setdefault(key, value)
            data["data_json"] = raw_data_json
        data["id"] = model.id
        data["imie"] = data.get("imiona", "")
        data["first_name"] = data.get("imiona", "")
        data["last_name"] = data.get("nazwisko", "")
        data["zamieszkanie_lubuskie"] = data.get("zamieszkuje_lubuskie", "")
        data["praca_lubuskie"] = data.get("pracuje_lubuskie", "")
        data["osoba_z_niepelnosprawnosciami"] = data.get("osoba_niepelnosprawna", "")
        data["accept_regulamin"] = data.get("osw_regulamin", "")
        data["accept_rodo"] = data.get("osw_rodo", "")
        data["accept_odpowiedzialnosc"] = data.get("osw_prawdziwosc", "")
        return data
