from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from io import StringIO

import pytest
from sqlalchemy import select

from database import create_session_factory
from models import (
    Form, FormSubmission, FormVersion, SubmissionTraining, TrainingAttendanceRecord,
    TrainingAttendanceSession, TrainingSurvey, TrainingSurveyInvitation,
)
from test_admin_panel import admin_app, create_form, create_user, grant_rbac, login
from routes.admin.training_management import _csv_safe


def _definition():
    return {"title": "Szkolenia", "fields": [], "documents": [{"id": "declaration", "fields": [{
        "type": "training_selection", "name": "selected_trainings", "enabled": True,
        "currency": "PLN", "max_total_amount": "5000", "catalog": [
            {"id": "excel", "name": "Excel", "price": "100", "active": True,
             "dates": [{"start_date": "2026-10-12", "end_date": "2026-10-13"}]},
            {"id": "archive", "name": "Archiwalne", "price": "100", "active": False,
             "archived": True, "dates": [{"start_date": "2025-05-01"}]},
            {"id": "empty", "name": "Puste", "price": "100", "active": True},
        ],
    }]}]}


@pytest.fixture()
def export_env(admin_app):
    admin_id = create_user(admin_app)
    form_id = create_form(admin_app, slug="export-training", definition_json=_definition())
    factory = create_session_factory(admin_app.config["DATABASE_URL"])
    with factory.begin() as db:
        form = db.get(Form, form_id)
        old = FormVersion(form_id=form_id, version_major=1, version_minor=0,
                          version_label="1.0", status="archived", definition_json=_definition())
        current = FormVersion(form_id=form_id, version_major=1, version_minor=1,
                              version_label="1.1", status="published", definition_json=_definition())
        db.add_all([old, current])
        db.flush()
        people = [
            ("SUB-1", "Żaneta", "Kowalska", "zaneta@example.org", "+48123456789", old.id,
             "excel", "agreement_signed_by_office", "Excel historyczny"),
            ("SUB-2", "=FORMULA", "Nowak", "nowak@example.org", "123", current.id,
             "excel", "selected", "Excel nowy"),
            ("SUB-3", "Anna", "Zielińska", "anna@example.org", "456", old.id,
             "archive", "cancelled", "Archiwalne"),
            ("SUB-4", "Piotr", "Wiśniewski", "piotr@example.org", "789", old.id,
             "history-only", "selected", "Historyczne; szkolenie"),
        ]
        participants = {}
        for public_id, first, last, email, phone, version_id, training_id, status, training_name in people:
            submission = FormSubmission(submission_id=public_id, form_slug=form.slug,
                form_version_id=version_id, imiona=first, nazwisko=last, email=email, telefon=phone)
            db.add(submission)
            db.flush()
            dates = [{"start_date": "2026-10-12", "end_date": "2026-10-13"}] if training_id == "excel" else []
            participant = SubmissionTraining(submission_id=submission.id, training_id=training_id,
                training_name_snapshot=training_name, training_snapshot={"id": training_id,
                    "name": training_name, "dates": dates}, status=status,
                selected_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
            db.add(participant)
            db.flush()
            participants[public_id] = participant.id
        session = TrainingAttendanceSession(form_id=form_id, training_id="excel",
            name="Spotkanie", session_date=date(2026, 10, 12))
        survey = TrainingSurvey(form_id=form_id, training_id="excel", name="Ankieta")
        db.add_all([session, survey])
        db.flush()
        db.add(TrainingAttendanceRecord(session_id=session.id,
            submission_training_id=participants["SUB-1"], status="present"))
        db.add(TrainingSurveyInvitation(survey_id=survey.id,
            submission_training_id=participants["SUB-1"], token_hash="a" * 64,
            completed_at=datetime(2026, 10, 15, tzinfo=timezone.utc)))
    client = admin_app.test_client()
    assert login(client).status_code == 302
    return admin_app, client, factory, form_id, participants, admin_id


def _rows(response):
    assert response.status_code == 200
    assert response.data.startswith(b"\xef\xbb\xbf")
    return list(csv.reader(StringIO(response.data.decode("utf-8-sig")), delimiter=";"))


def _url(form_id, training_id, suffix=""):
    return f"/admin/forms/{form_id}/training-management/{training_id}/participants.csv{suffix}"


@pytest.mark.parametrize("value", ["=SUM(1;2)", "+481234", "-CMD", "@HYPERLINK", "  =1+1", "\t=1+1", "\n@x", "\ufeff=1+1"])
def test_csv_formula_prefix_is_neutralized(value):
    assert _csv_safe(value).startswith("'")


def test_active_training_csv_has_polish_headers_snapshot_values_and_no_writes(export_env):
    _app, client, factory, form_id, participants, _admin = export_env
    with factory() as db:
        before = [(row.id, row.status, row.training_snapshot, row.updated_at)
                  for row in db.execute(select(SubmissionTraining).order_by(SubmissionTraining.id)).scalars()]
    response = client.get(_url(form_id, "excel"))
    rows = _rows(response)
    assert len(rows) == 3
    assert len(rows[0]) == 14
    assert rows[0][:4] == ["Lp.", "ID zgłoszenia", "Imię i nazwisko", "Adres e-mail"]
    assert {row[1] for row in rows[1:]} == {"SUB-1", "SUB-2"}
    first = next(row for row in rows[1:] if row[1] == "SUB-1")
    assert first[2:8] == ["Żaneta Kowalska", "zaneta@example.org", "'+48123456789",
                          "Excel historyczny", "excel", "12.10.2026–13.10.2026"]
    assert first[8:] == ["Umowa podpisana przez urząd", "01.09.2026", "Podpisana",
                          "Obecny", "Tak", "Wypełniona"]
    assert next(row for row in rows[1:] if row[1] == "SUB-2")[2].startswith("'=FORMULA")
    assert response.mimetype == "text/csv"
    assert "charset=utf-8" in response.headers["Content-Type"]
    assert "attachment" in response.headers["Content-Disposition"]
    assert "uczestnicy_szkolenia_excel_" in response.headers["Content-Disposition"]
    assert "no-store" in response.headers["Cache-Control"]
    with factory() as db:
        after = [(row.id, row.status, row.training_snapshot, row.updated_at)
                 for row in db.execute(select(SubmissionTraining).order_by(SubmissionTraining.id)).scalars()]
        assert after == before
        assert len(participants) == 4


def test_archived_historical_and_empty_trainings_export(export_env):
    _app, client, _factory, form_id, _participants, _admin = export_env
    archive = _rows(client.get(_url(form_id, "archive")))
    historical = _rows(client.get(_url(form_id, "history-only")))
    empty = _rows(client.get(_url(form_id, "empty")))
    assert archive[1][1:3] == ["SUB-3", "Anna Zielińska"]
    assert historical[1][5:7] == ["Historyczne; szkolenie", "history-only"]
    assert '"Historyczne; szkolenie"' in client.get(_url(form_id, "history-only")).data.decode("utf-8-sig")
    assert len(empty) == 1
    assert client.get(_url(form_id, "missing")).status_code == 404


@pytest.mark.parametrize("filter_query,expected", [
    ("status=selected", "SUB-2"),
    ("agreement=signed", "SUB-1"),
    ("attendance=present", "SUB-1"),
    ("survey_sent=yes", "SUB-1"),
    ("survey_sent=no", "SUB-2"),
    ("query=Kowalska", "SUB-1"),
    ("status=selected&agreement=pending&attendance=pending&survey_sent=no&query=Nowak", "SUB-2"),
])
def test_export_uses_participant_filters_and_all_scope(export_env, filter_query, expected):
    _app, client, _factory, form_id, _participants, _admin = export_env
    rows = _rows(client.get(_url(form_id, "excel", "?" + filter_query)))
    assert [row[1] for row in rows[1:]] == [expected]
    all_rows = _rows(client.get(_url(form_id, "excel", "?scope=all&" + filter_query)))
    assert {row[1] for row in all_rows[1:]} == {"SUB-1", "SUB-2"}


def test_export_links_cover_current_archived_and_detail_filters(export_env):
    _app, client, _factory, form_id, _participants, _admin = export_env
    current = client.get(f"/admin/forms/{form_id}/training-management").text
    archived = client.get(f"/admin/forms/{form_id}/training-management?archived=1").text
    detail = client.get(f"/admin/forms/{form_id}/training-management/excel?status=selected").text
    assert "Eksportuj uczestników (CSV)" in current
    assert archived.count("Eksportuj uczestników (CSV)") == 2
    assert "Eksportuj uczestników (CSV)" in detail
    assert "Eksportuj całą listę (CSV)" in detail
    assert "status=selected" in detail


def test_export_requires_both_export_and_sensitive_data_permissions(export_env):
    app, _client, _factory, form_id, _participants, _admin = export_env
    cases = [
        ({"can_export_data", "can_view_sensitive_data"}, 403),
        ({"can_view_submissions", "can_export_data"}, 403),
        ({"can_view_submissions", "can_view_sensitive_data"}, 403),
        ({"can_view_submissions", "can_export_data", "can_view_sensitive_data"}, 200),
    ]
    for index, (permissions, expected) in enumerate(cases):
        email = f"limited-{index}@example.org"
        user_id = create_user(app, email=email, role="admin")
        grant_rbac(app, user_id, sorted(permissions), form_id=form_id)
        client = app.test_client()
        assert login(client, email=email).status_code == 302
        response = client.get(_url(form_id, "excel"))
        assert response.status_code == expected
        if expected == 200:
            assert "Żaneta" in response.data.decode("utf-8-sig")
        else:
            assert "Żaneta" not in response.text
            listing = client.get(f"/admin/forms/{form_id}/training-management")
            if listing.status_code == 200:
                assert "Eksportuj uczestników (CSV)" not in listing.text
    anonymous = app.test_client()
    assert anonymous.get(_url(form_id, "excel")).status_code in {302, 401}


def test_export_rejects_training_from_another_form(export_env):
    app, client, _factory, _form_id, _participants, _admin = export_env
    other_form_id = create_form(app, slug="another-training-form", definition_json=_definition())
    assert client.get(_url(other_form_id, "history-only")).status_code == 404
