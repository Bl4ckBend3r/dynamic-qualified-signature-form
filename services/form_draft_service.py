from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import secrets
from uuid import uuid4

from sqlalchemy import or_, select, update

from form_loader import EMAIL_REGEX, extract_submission_data
from models import FormDraft


FORM_DRAFT_ACTIVE = "active"
FORM_DRAFT_SUBMITTED = "submitted"
FORM_DRAFT_EXPIRED = "expired"
FORM_DRAFT_ABANDONED = "abandoned"
SUBMIT_CLAIM_TIMEOUT = timedelta(minutes=15)


class FormDraftError(ValueError):
    pass


@dataclass(frozen=True)
class CreatedFormDraft:
    draft: FormDraft
    raw_token: str


class FormDraftService:
    def __init__(self, *, ttl_days: int = 30, max_data_bytes: int = 262_144) -> None:
        self.ttl_days = min(365, max(1, int(ttl_days)))
        self.max_data_bytes = min(1_048_576, max(4_096, int(max_data_bytes)))

    @staticmethod
    def token_hash(raw_token: str) -> str:
        return sha256(str(raw_token or "").encode("utf-8")).hexdigest()

    @staticmethod
    def generate_token() -> str:
        return secrets.token_urlsafe(48)

    def normalize_data(self, form_config: dict, request_data) -> dict:
        values = extract_submission_data(form_config, request_data or {})
        normalized: dict[str, str] = {}
        for field in form_config.get("fields") or []:
            if field.get("type") in {"section", "static_text", "file", "attachment"}:
                continue
            name = str(field.get("name") or "")
            if not name or name not in values:
                continue
            value = values[name]
            if isinstance(value, str):
                value = value.replace("\x00", "")[:20_000]
            normalized[name] = value
        encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > self.max_data_bytes:
            raise FormDraftError("Wersja robocza zawiera zbyt dużo danych.")
        return normalized

    @staticmethod
    def resolve_email(form_config: dict, data: dict) -> str:
        candidates = [
            str(field.get("name") or "")
            for field in form_config.get("fields") or []
            if field.get("type") == "email"
        ]
        candidates.extend(("email", "adres_email", "e_mail"))
        email = next((str(data.get(name) or "").strip().lower() for name in candidates if data.get(name)), "")
        if not email or not EMAIL_REGEX.fullmatch(email) or len(email) > 255:
            raise FormDraftError("Podaj poprawny adres e-mail, aby zapisać wersję roboczą.")
        return email

    def create(self, db, *, form, form_version, form_config: dict, request_data) -> CreatedFormDraft:
        data = self.normalize_data(form_config, request_data)
        email = self.resolve_email(form_config, data)
        raw_token = self.generate_token()
        now = datetime.now(timezone.utc)
        draft = FormDraft(
            public_id=str(uuid4()),
            form_id=form.id,
            form_version_id=form_version.id if form_version else None,
            email=email,
            data_json=data,
            status=FORM_DRAFT_ACTIVE,
            token_hash=self.token_hash(raw_token),
            expires_at=now + timedelta(days=self.ttl_days),
            last_autosave_at=now,
            metadata_json={"source": "public_form", "attachments_staged": False},
        )
        db.add(draft)
        db.flush()
        return CreatedFormDraft(draft, raw_token)

    def get_by_token(self, db, *, form_id: int, raw_token: str, require_active: bool = True) -> FormDraft | None:
        token_hash = self.token_hash(raw_token)
        draft = db.execute(
            select(FormDraft).where(FormDraft.form_id == form_id, FormDraft.token_hash == token_hash)
        ).scalar_one_or_none()
        if not draft:
            return None
        now = datetime.now(timezone.utc)
        expires_at = self._aware(draft.expires_at)
        if draft.status == FORM_DRAFT_ACTIVE and expires_at <= now:
            draft.status = FORM_DRAFT_EXPIRED
            draft.submit_started_at = None
            db.commit()
            return None
        if require_active and draft.status != FORM_DRAFT_ACTIVE:
            return None
        return draft

    def autosave(self, db, *, draft: FormDraft, form_config: dict, request_data) -> FormDraft:
        if draft.status != FORM_DRAFT_ACTIVE or self._aware(draft.expires_at) <= datetime.now(timezone.utc):
            raise FormDraftError("Wersja robocza wygasła albo została już wysłana.")
        draft.data_json = self.normalize_data(form_config, request_data)
        # The recovery address is required only at creation. A partially edited
        # form may temporarily contain an empty email field; retain the last
        # verified address in that case so autosave can remain incomplete.
        email_values = [
            str(draft.data_json.get(str(field.get("name") or "")) or "").strip()
            for field in form_config.get("fields") or []
            if field.get("type") == "email"
        ]
        if any(email_values):
            draft.email = self.resolve_email(form_config, draft.data_json)
        draft.last_autosave_at = datetime.now(timezone.utc)
        db.flush()
        return draft

    def rotate_for_email(self, db, *, form_id: int, email: str) -> CreatedFormDraft | None:
        normalized_email = str(email or "").strip().lower()
        if not EMAIL_REGEX.fullmatch(normalized_email) or len(normalized_email) > 255:
            return None
        now = datetime.now(timezone.utc)
        candidates = db.execute(
            select(FormDraft)
            .where(FormDraft.form_id == form_id, FormDraft.email == normalized_email, FormDraft.status == FORM_DRAFT_ACTIVE)
            .order_by(FormDraft.updated_at.desc(), FormDraft.id.desc())
        ).scalars().all()
        draft = next((item for item in candidates if self._aware(item.expires_at) > now), None)
        if not draft:
            return None
        raw_token = self.generate_token()
        draft.token_hash = self.token_hash(raw_token)
        draft.updated_at = now
        db.flush()
        return CreatedFormDraft(draft, raw_token)

    def claim_for_submit(self, db, *, draft_id: int, raw_token: str) -> FormDraft | None:
        now = datetime.now(timezone.utc)
        stale_before = now - SUBMIT_CLAIM_TIMEOUT
        result = db.execute(
            update(FormDraft)
            .where(
                FormDraft.id == draft_id,
                FormDraft.token_hash == self.token_hash(raw_token),
                FormDraft.status == FORM_DRAFT_ACTIVE,
                FormDraft.expires_at > now,
                or_(FormDraft.submit_started_at.is_(None), FormDraft.submit_started_at < stale_before),
            )
            .values(submit_started_at=now, updated_at=now)
        )
        if result.rowcount != 1:
            db.rollback()
            return None
        db.commit()
        draft = db.get(FormDraft, draft_id)
        if draft and not draft.submission_public_id:
            draft.submission_public_id = str(uuid4())
            db.commit()
        return draft

    @staticmethod
    def release_submit_claim(db, draft_id: int) -> None:
        db.execute(
            update(FormDraft)
            .where(FormDraft.id == draft_id, FormDraft.status == FORM_DRAFT_ACTIVE)
            .values(submit_started_at=None)
        )
        db.commit()

    @staticmethod
    def complete(db, *, draft_id: int, submission_internal_id: int) -> None:
        now = datetime.now(timezone.utc)
        result = db.execute(
            update(FormDraft)
            .where(FormDraft.id == draft_id, FormDraft.status == FORM_DRAFT_ACTIVE, FormDraft.submission_id.is_(None))
            .values(
                status=FORM_DRAFT_SUBMITTED,
                completed_at=now,
                submit_started_at=None,
                submission_id=submission_internal_id,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            db.rollback()
            raise FormDraftError("Wersja robocza została już wysłana.")
        db.commit()

    @staticmethod
    def mark_expired(db) -> int:
        now = datetime.now(timezone.utc)
        result = db.execute(
            update(FormDraft)
            .where(FormDraft.status == FORM_DRAFT_ACTIVE, FormDraft.expires_at <= now)
            .values(status=FORM_DRAFT_EXPIRED, submit_started_at=None, updated_at=now)
        )
        db.commit()
        return int(result.rowcount or 0)

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
