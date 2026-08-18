from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from models import (
    Form,
    FormPermission,
    FormSubmission,
    SubmissionInternalNote,
    SubmissionInternalNoteMention,
    SubmissionInternalNoteRevision,
    User,
)


MAX_NOTE_LENGTH = 5000
EMAIL_MENTION_RE = re.compile(r"(?<!\w)@([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
HANDLE_MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9._-]{2,64})(?!@)")


class SubmissionInternalNoteError(ValueError):
    pass


class SubmissionInternalNotePermissionError(SubmissionInternalNoteError):
    pass


class SubmissionInternalNoteService:
    """Owns private officer notes. Nothing here participates in public submission contexts."""

    def __init__(self, audit_log_service=None) -> None:
        self.audit_log_service = audit_log_service

    @staticmethod
    def _permission(db, user: User, form: Form) -> FormPermission | None:
        if user.role == "super_admin":
            return None
        return db.execute(select(FormPermission).where(
            FormPermission.user_id == user.id,
            FormPermission.form_id == form.id,
        )).scalar_one_or_none()

    def can_view(self, db, user: User, form: Form) -> bool:
        if user.role == "super_admin":
            return True
        permission = self._permission(db, user, form)
        return bool(user.is_active and not user.is_blocked and permission and permission.can_view_internal_notes)

    def can_add(self, db, user: User, form: Form) -> bool:
        if user.role == "super_admin":
            return True
        permission = self._permission(db, user, form)
        return bool(user.is_active and not user.is_blocked and permission and permission.can_view_internal_notes and permission.can_add_internal_notes)

    def can_manage(self, db, user: User, form: Form) -> bool:
        if user.role == "super_admin":
            return True
        permission = self._permission(db, user, form)
        return bool(user.is_active and not user.is_blocked and permission and permission.can_view_internal_notes and permission.can_manage_internal_notes)

    def list_notes(self, db, submission: FormSubmission, *, search: str = "", form: Form | None = None,
                   viewer: User | None = None) -> list[SubmissionInternalNote]:
        if form is not None or viewer is not None:
            if form is None or viewer is None or not self.can_view(db, viewer, form):
                raise SubmissionInternalNotePermissionError("Brak uprawnienia do wyświetlania notatek wewnętrznych.")
            self._require_submission_form(submission, form)
        query = select(SubmissionInternalNote).options(
            selectinload(SubmissionInternalNote.author),
            selectinload(SubmissionInternalNote.archived_by),
            selectinload(SubmissionInternalNote.revisions).selectinload(SubmissionInternalNoteRevision.edited_by),
            selectinload(SubmissionInternalNote.mentions).selectinload(SubmissionInternalNoteMention.mentioned_user),
        ).where(SubmissionInternalNote.submission_id == submission.id)
        wanted = str(search or "").strip()
        if wanted:
            query = query.where(
                SubmissionInternalNote.archived_at.is_(None),
                SubmissionInternalNote.content.ilike(f"%{self._escape_like(wanted)}%", escape="\\"),
            )
        query = query.order_by(
            SubmissionInternalNote.archived_at.is_not(None).asc(),
            SubmissionInternalNote.is_important.desc(),
            SubmissionInternalNote.created_at.desc(),
            SubmissionInternalNote.id.desc(),
        )
        return db.execute(query).scalars().all()

    def create(self, db, submission: FormSubmission, form: Form, *, author: User, content: str,
               is_important: bool = False, parent_note_id: int | None = None) -> SubmissionInternalNote:
        if not self.can_add(db, author, form):
            raise SubmissionInternalNotePermissionError("Brak uprawnienia do dodawania notatek wewnętrznych.")
        self._require_submission_form(submission, form)
        cleaned = self._validate_content(content)
        parent = None
        if parent_note_id is not None:
            parent = db.get(SubmissionInternalNote, parent_note_id)
            if not parent or parent.submission_id != submission.id:
                raise SubmissionInternalNoteError("Notatka nadrzędna nie należy do tego zgłoszenia.")
        note = SubmissionInternalNote(
            submission_id=submission.id,
            author_user_id=author.id,
            content=cleaned,
            is_important=bool(is_important),
            parent_note_id=parent.id if parent else None,
        )
        db.add(note)
        db.flush()
        mentioned_users = self._sync_mentions(db, note, form)
        self._audit("internal_note_added", submission, author, note, {"important": note.is_important, "mentions": len(mentioned_users)})
        return note

    def edit(self, db, submission: FormSubmission, form: Form, note: SubmissionInternalNote, *,
             editor: User, content: str, is_important: bool | None = None) -> SubmissionInternalNote:
        if not self.can_manage(db, editor, form):
            raise SubmissionInternalNotePermissionError("Brak uprawnienia do zarządzania notatkami wewnętrznymi.")
        self._require_note_submission(note, submission)
        if note.archived_at is not None:
            raise SubmissionInternalNoteError("Zarchiwizowanej notatki nie można edytować.")
        cleaned = self._validate_content(content)
        now = datetime.now(timezone.utc)
        target_important = note.is_important if is_important is None else bool(is_important)
        changed = cleaned != note.content or target_important != note.is_important
        if changed:
            db.add(SubmissionInternalNoteRevision(
                note_id=note.id,
                previous_content=note.content,
                new_content=cleaned,
                previous_is_important=note.is_important,
                new_is_important=target_important,
                edited_by_user_id=editor.id,
                edited_at=now,
            ))
            note.content = cleaned
            note.edited_at = now
            note.is_important = target_important
        db.flush()
        mentioned_users = self._sync_mentions(db, note, form)
        if changed:
            self._audit("internal_note_revised", submission, editor, note, {"mentions": len(mentioned_users)})
        return note

    def archive(self, db, submission: FormSubmission, form: Form, note: SubmissionInternalNote, *, actor: User) -> None:
        if not self.can_manage(db, actor, form):
            raise SubmissionInternalNotePermissionError("Brak uprawnienia do archiwizacji notatek wewnętrznych.")
        self._require_note_submission(note, submission)
        if note.archived_at is None:
            note.archived_at = datetime.now(timezone.utc)
            note.archived_by_user_id = actor.id
            self._audit("internal_note_archived", submission, actor, note, {})

    def _sync_mentions(self, db, note: SubmissionInternalNote, form: Form) -> list[User]:
        users = db.execute(
            select(User).outerjoin(FormPermission, FormPermission.user_id == User.id).where(
                User.is_active.is_(True),
                User.is_blocked.is_(False),
                or_(User.role == "super_admin", FormPermission.form_id == form.id),
            ).distinct()
        ).scalars().all()
        by_email = {user.email.casefold(): user for user in users}
        by_handle: dict[str, list[User]] = {}
        for user in users:
            by_handle.setdefault(user.email.split("@", 1)[0].casefold(), []).append(user)
        selected: dict[int, User] = {}
        for token in EMAIL_MENTION_RE.findall(note.content):
            user = by_email.get(token.casefold())
            if user:
                selected[user.id] = user
        for token in HANDLE_MENTION_RE.findall(note.content):
            matches = by_handle.get(token.casefold(), [])
            if len(matches) == 1:
                selected[matches[0].id] = matches[0]
        note.mentions.clear()
        db.flush()
        for user in selected.values():
            note.mentions.append(SubmissionInternalNoteMention(mentioned_user_id=user.id))
        db.flush()
        return list(selected.values())

    def _audit(self, event_type: str, submission: FormSubmission, actor: User,
               note: SubmissionInternalNote, metadata: dict) -> None:
        if not self.audit_log_service:
            return
        self.audit_log_service.log_event(
            event_type,
            submission.submission_id,
            submission.form_slug,
            actor=actor.email,
            metadata={"note_id": note.id, **metadata},
        )

    @staticmethod
    def _validate_content(content: str) -> str:
        cleaned = str(content or "").strip()
        if not cleaned:
            raise SubmissionInternalNoteError("Treść notatki jest wymagana.")
        if len(cleaned) > MAX_NOTE_LENGTH:
            raise SubmissionInternalNoteError(f"Notatka może mieć maksymalnie {MAX_NOTE_LENGTH} znaków.")
        if "\x00" in cleaned:
            raise SubmissionInternalNoteError("Treść notatki zawiera niedozwolony znak.")
        return cleaned

    @staticmethod
    def _require_submission_form(submission: FormSubmission, form: Form) -> None:
        if submission.form_slug != form.slug:
            raise SubmissionInternalNoteError("Zgłoszenie nie należy do tego formularza.")

    @staticmethod
    def _require_note_submission(note: SubmissionInternalNote, submission: FormSubmission) -> None:
        if note.submission_id != submission.id:
            raise SubmissionInternalNoteError("Notatka nie należy do tego zgłoszenia.")

    @staticmethod
    def _escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
