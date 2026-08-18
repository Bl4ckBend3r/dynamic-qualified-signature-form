import json
from pathlib import Path

import pytest
from markupsafe import escape
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from models import (
    Base, Form, FormPermission, FormSubmission, SubmissionInternalNoteRevision, User,
)
from services.mail_template_service import build_mail_context
from services.public_submission_status_service import build_public_submission_status
from services.submission_internal_note_service import (
    SubmissionInternalNoteError, SubmissionInternalNotePermissionError, SubmissionInternalNoteService,
)


class AuditCapture:
    def __init__(self):
        self.events = []

    def log_event(self, *args, **kwargs):
        self.events.append((args, kwargs))


@pytest.fixture()
def note_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'notes.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session.begin() as db:
        form = Form(slug="notes", name="Notes", definition_json={})
        author = User(email="anna@example.test", password_hash="x", role="admin")
        mentioned = User(email="jan@example.test", password_hash="x", role="form_manager")
        viewer = User(email="viewer@example.test", password_hash="x", role="form_manager")
        denied = User(email="denied@example.test", password_hash="x", role="form_manager")
        db.add_all([form, author, mentioned, viewer, denied]); db.flush()
        db.add_all([
            FormPermission(user_id=author.id, form_id=form.id, can_manage=False, can_review=True, can_view_internal_notes=True, can_add_internal_notes=True, can_manage_internal_notes=True),
            FormPermission(user_id=mentioned.id, form_id=form.id, can_manage=False, can_review=True, can_view_internal_notes=True, can_add_internal_notes=True, can_manage_internal_notes=False),
            FormPermission(user_id=viewer.id, form_id=form.id, can_manage=False, can_review=False, can_view_internal_notes=True, can_add_internal_notes=False, can_manage_internal_notes=False),
            FormPermission(user_id=denied.id, form_id=form.id, can_manage=False, can_review=False, can_view_internal_notes=False, can_add_internal_notes=False, can_manage_internal_notes=False),
        ])
        first = FormSubmission(submission_id="note-1", form_slug=form.slug, form_name=form.name)
        second = FormSubmission(submission_id="note-2", form_slug=form.slug, form_name=form.name)
        db.add_all([first, second]); db.flush()
        ids = {name: value.id for name, value in {"form": form, "author": author, "mentioned": mentioned, "viewer": viewer, "denied": denied, "first": first, "second": second}.items()}
    yield Session, ids
    engine.dispose()


def test_create_view_important_mentions_and_audit_without_content(note_db):
    Session, ids = note_db
    audit = AuditCapture(); service = SubmissionInternalNoteService(audit)
    with Session.begin() as db:
        note = service.create(
            db, db.get(FormSubmission, ids["first"]), db.get(Form, ids["form"]),
            author=db.get(User, ids["author"]), content="Pilne dla @jan oraz @jan@example.test", is_important=True,
        )
        assert note.is_important
        assert [mention.mentioned_user_id for mention in note.mentions] == [ids["mentioned"]]
        assert service.list_notes(db, db.get(FormSubmission, ids["first"]))[0].id == note.id
        serialized_audit = json.dumps(audit.events, default=str)
        assert "internal_note_added" in serialized_audit
        assert "Pilne dla" not in serialized_audit


def test_permissions_other_submission_revision_archive_and_immutable_history(note_db):
    Session, ids = note_db
    service = SubmissionInternalNoteService()
    with Session.begin() as db:
        form = db.get(Form, ids["form"]); first = db.get(FormSubmission, ids["first"]); second = db.get(FormSubmission, ids["second"])
        author = db.get(User, ids["author"]); denied = db.get(User, ids["denied"])
        with pytest.raises(SubmissionInternalNotePermissionError):
            service.create(db, first, form, author=denied, content="Brak dostępu")
        with pytest.raises(SubmissionInternalNotePermissionError):
            service.list_notes(db, first, form=form, viewer=denied)
        note = service.create(db, first, form, author=author, content="Pierwsza wersja")
        with pytest.raises(SubmissionInternalNoteError, match="tego zgłoszenia"):
            service.edit(db, second, form, note, editor=author, content="Próba zmiany")
        service.edit(db, first, form, note, editor=author, content="Druga wersja", is_important=True)
        db.flush()
        revision = db.execute(select(SubmissionInternalNoteRevision)).scalar_one()
        assert (revision.previous_content, revision.new_content) == ("Pierwsza wersja", "Druga wersja")
        assert (revision.previous_is_important, revision.new_is_important) == (False, True)
        note_id = note.id
    with Session() as db:
        revision = db.execute(select(SubmissionInternalNoteRevision)).scalar_one()
        revision.new_content = "Cicha zmiana"
        with pytest.raises(ValueError, match="immutable"):
            db.flush()
        db.rollback()
        note = service.list_notes(db, db.get(FormSubmission, ids["first"]))[0]
        note.content = "Cicha zmiana bez rewizji"
        with pytest.raises(ValueError, match="without a revision"):
            db.flush()
        db.rollback()
    with Session.begin() as db:
        form = db.get(Form, ids["form"]); first = db.get(FormSubmission, ids["first"]); author = db.get(User, ids["author"])
        note = service.list_notes(db, first)[0]
        assert note.id == note_id
        service.archive(db, first, form, note, actor=author)
        assert note.archived_at is not None and note.archived_by_user_id == author.id


def test_search_and_xss_remain_plain_text(note_db):
    Session, ids = note_db
    service = SubmissionInternalNoteService()
    payload = '<script>alert("x")</script>'
    with Session.begin() as db:
        form = db.get(Form, ids["form"]); submission = db.get(FormSubmission, ids["first"]); author = db.get(User, ids["author"])
        note = service.create(db, submission, form, author=author, content=f"Zaświadczenie {payload}")
        assert service.list_notes(db, submission, search="Zaświadczenie")[0].id == note.id
        assert "<script>" not in str(escape(note.content))
    template = Path("templates/admin/submissions/detail.html").read_text(encoding="utf-8")
    assert "{{ note.content }}" in template
    assert "note.content|safe" not in template and "note.content|trusted_html" not in template


def test_internal_notes_are_not_in_public_or_mail_context(note_db):
    Session, ids = note_db
    secret = "TAJNA NOTATKA URZĘDNIKA"
    with Session.begin() as db:
        form = db.get(Form, ids["form"]); submission = db.get(FormSubmission, ids["first"]); author = db.get(User, ids["author"])
        SubmissionInternalNoteService().create(db, submission, form, author=author, content=secret)
        public = build_public_submission_status({"process_status": "FORM_SUBMITTED", "internal_notes": [secret]})
        mail = build_mail_context(form, submission)
        assert secret not in json.dumps(public, ensure_ascii=False, default=str)
        assert "internal_notes" not in mail
        assert secret not in json.dumps(mail, ensure_ascii=False, default=str)
        assert "internal_notes" not in {column.name for column in FormSubmission.__table__.columns}


def test_submission_cascade_can_remove_notes_without_user_hard_delete_action(note_db):
    Session, ids = note_db
    service = SubmissionInternalNoteService()
    with Session.begin() as db:
        form = db.get(Form, ids["form"]); submission = db.get(FormSubmission, ids["first"]); author = db.get(User, ids["author"])
        note = service.create(db, submission, form, author=author, content="Notatka przed usunięciem całej sprawy")
        service.edit(db, submission, form, note, editor=author, content="Poprawiona przed usunięciem")
        db.delete(submission)
    with Session() as db:
        assert db.execute(select(SubmissionInternalNoteRevision)).scalars().all() == []
