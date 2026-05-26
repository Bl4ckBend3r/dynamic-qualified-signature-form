from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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
