from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import importlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, MetaData, String, Table, Text, create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

from models import Base, ConsentVersion, Form, FormField, FormRegulation, FormSubmission, SubmissionConsent
from repositories.submission_repository import PostgresSubmissionRepository
from services.compliance_service import ComplianceService, consent_sha256, file_sha256, normalize_consent_text
from services.form_version_service import FORM_VERSION_DRAFT, FormVersionService


def _database(tmp_path):
    url = f"sqlite:///{tmp_path / 'compliance.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return url, Session


def _definition(text="Akceptuję zasady programu.", *, required=True):
    return {
        "title": "Formularz",
        "fields": [
            {
                "name": "accept_terms",
                "label": "Regulamin programu",
                "type": "checkbox",
                "required": required,
                "consent_type": "regulation",
                "options": [{"value": "Tak", "label": text}],
            },
            {
                "name": "marketing",
                "label": "Kontakt marketingowy",
                "type": "checkbox",
                "required": False,
                "consent_type": "data_processing",
                "options": [{"value": "Tak", "label": "Zgadzam się na kontakt."}],
            },
        ],
        "workflow": {"initial_step": "submission", "steps": [{"id": "submission"}]},
    }


def _form(db, tmp_path):
    definition = _definition()
    form = Form(slug="compliance", name="Compliance", title="Compliance", definition_json=definition, is_active=True, is_public=True)
    db.add(form)
    db.flush()
    for order, field in enumerate(definition["fields"]):
        db.add(FormField(form_id=form.id, name=field["name"], label=field["label"], type="checkbox", required=field["required"], options=field["options"], default_value="", section="Zgody", stage="initial_submission", sort_order=order, active=True))
    regulation_bytes = b"%PDF-1.4\nimmutable regulation"
    regulation_path = tmp_path / "regulation.pdf"
    regulation_path.write_bytes(regulation_bytes)
    regulation = FormRegulation(form_id=form.id, original_filename="regulation.pdf", storage_path=str(regulation_path), mime_type="application/pdf", size_bytes=len(regulation_bytes))
    db.add(regulation)
    db.flush()
    return form, regulation_bytes


def _submission(db, form, version, public_id):
    item = FormSubmission(submission_id=public_id, form_slug=form.slug, form_name=form.name, form_version_id=version.id, data_json={}, access_token="token")
    db.add(item)
    db.flush()
    return item


def test_hash_normalization_is_stable_and_file_hash_uses_bytes(tmp_path):
    assert normalize_consent_text("Zażółć  \r\n gęślą\r\n") == "Zażółć\n gęślą"
    assert consent_sha256("Zażółć  \r\n gęślą\r\n") == consent_sha256("Zażółć\n gęślą")
    path = tmp_path / "terms.bin"
    path.write_bytes(b"terms-bytes")
    assert file_sha256(path) == sha256(b"terms-bytes").hexdigest()


def test_v1_v2_and_submission_snapshots_remain_historical(tmp_path):
    url, Session = _database(tmp_path)
    version_service = FormVersionService()
    compliance = ComplianceService(PostgresSubmissionRepository(url, session_factory=Session))
    with Session.begin() as db:
        form, regulation_bytes = _form(db, tmp_path)
        first = version_service.create_initial_version(db, form, actor_id=None, status=FORM_VERSION_DRAFT)
        assert db.execute(select(ConsentVersion).where(ConsentVersion.status == "draft")).scalars().all()
        regulation = db.execute(select(FormRegulation).where(FormRegulation.form_id == form.id)).scalar_one()
        staged_regulation = compliance.stage_regulation_version(db, form, regulation, actor_id=None)
        staged_regulation_id = staged_regulation.id
        assert staged_regulation.status == "draft"
        version_service.publish(db, form, first, actor_id=None)
        first_id = first.id
        first_consent_id = first.consent_links[0].consent_version_id
        assert first.regulation_version.sha256 == sha256(regulation_bytes).hexdigest()
        assert first.regulation_version.id == staged_regulation_id
        assert staged_regulation.status == "published"
        _submission(db, form, first, "submission-a")

    compliance.record_submission_acceptances(submission_id="submission-a", form_version_id=first_id, submission_data={"accept_terms": "Tak", "marketing": "Nie"})

    with Session.begin() as db:
        form = db.execute(select(Form).where(Form.slug == "compliance")).scalar_one()
        first = db.get(type(form.versions[0]), first_id)
        draft = version_service.clone_to_draft(db, form, first, actor_id=None, bump="minor")
        changed = deepcopy(draft.definition_json)
        terms_field = next(field for field in changed["fields"] if field.get("name") == "accept_terms")
        terms_field["options"][0]["label"] = "Akceptuję nowe zasady programu v2."
        version_service.update_definition(draft, changed)
        version_service.publish(db, form, draft, actor_id=None)
        second_id = draft.id
        second_consent_id = draft.consent_links[0].consent_version_id
        assert second_consent_id != first_consent_id
        _submission(db, form, draft, "submission-b")

    compliance.record_submission_acceptances(submission_id="submission-b", form_version_id=second_id, submission_data={"accept_terms": True, "marketing": False})

    with Session() as db:
        a = db.execute(select(SubmissionConsent).join(FormSubmission).where(FormSubmission.submission_id == "submission-a", SubmissionConsent.consent_key == "accept_terms")).scalar_one()
        b = db.execute(select(SubmissionConsent).join(FormSubmission).where(FormSubmission.submission_id == "submission-b", SubmissionConsent.consent_key == "accept_terms")).scalar_one()
        assert a.consent_version == "1.0"
        assert b.consent_version == "1.1"
        assert a.consent_text_snapshot == "Akceptuję zasady programu."
        assert b.consent_text_snapshot == "Akceptuję nowe zasady programu v2."
        assert a.consent_sha256 != b.consent_sha256
        assert a.accepted_at is not None
        optional = db.execute(select(SubmissionConsent).where(SubmissionConsent.submission_id == a.submission_id, SubmissionConsent.consent_key == "marketing")).scalar_one()
        assert optional.accepted is False
        assert optional.accepted_at is None
        assert "SHA-256" in compliance.audit_description(a)


def test_required_consent_blocks_submit_but_optional_may_be_false():
    service = ComplianceService()
    assert "accept_terms" in service.validate_acceptances(_definition(), {"accept_terms": False, "marketing": False})
    assert service.validate_acceptances(_definition(), {"accept_terms": True, "marketing": False}) == {}


def test_published_content_and_submission_snapshot_are_immutable(tmp_path):
    _url, Session = _database(tmp_path)
    version_service = FormVersionService()
    with Session.begin() as db:
        form, _bytes = _form(db, tmp_path)
        version = version_service.create_initial_version(db, form, actor_id=None, status=FORM_VERSION_DRAFT)
        version_service.publish(db, form, version, actor_id=None)
        consent = version.consent_links[0].consent_version
        _submission(db, form, version, "immutable")
        snapshot = SubmissionConsent(submission_id=1, consent_version_id=consent.id, consent_key="accept_terms", consent_version=consent.version_label, consent_title_snapshot=consent.title, consent_text_snapshot=consent.text_snapshot, consent_sha256=consent.sha256, accepted=True)
        db.add(snapshot)

    with Session() as db:
        consent = db.execute(select(ConsentVersion).where(ConsentVersion.status == "published")).scalars().first()
        consent.text_snapshot = "podmieniona treść"
        with pytest.raises(ValueError, match="nie można edytować"):
            db.commit()
        db.rollback()
        snapshot = db.execute(select(SubmissionConsent).where(SubmissionConsent.consent_key == "accept_terms")).scalar_one()
        snapshot.accepted = False
        with pytest.raises(ValueError, match="nie można edytować"):
            db.commit()


def test_migration_backfills_technical_snapshots_without_fake_acceptance_date(tmp_path, monkeypatch):
    migration = importlib.import_module("migrations.versions.20260813_0032_compliance_versioning")
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table("users", metadata, Column("id", Integer, primary_key=True))
    forms = Table("forms", metadata, Column("id", Integer, primary_key=True), Column("name", String(255), nullable=False))
    regulations = Table("form_regulations", metadata, Column("id", Integer, primary_key=True), Column("form_id", Integer, ForeignKey("forms.id"), nullable=False), Column("original_filename", String(512), nullable=False), Column("storage_path", Text, nullable=False), Column("mime_type", String(255), nullable=False), Column("size_bytes", Integer, nullable=False), Column("uploaded_by_user_id", Integer))
    form_versions = Table("form_versions", metadata, Column("id", Integer, primary_key=True), Column("form_id", Integer, ForeignKey("forms.id"), nullable=False), Column("version_major", Integer), Column("version_minor", Integer), Column("status", String(32)), Column("definition_json", JSON))
    submissions = Table("form_submissions", metadata, Column("id", Integer, primary_key=True), Column("form_version_id", Integer, ForeignKey("form_versions.id")), Column("accept_terms", Boolean), Column("data_json", JSON))
    metadata.create_all(engine)
    regulation_path = tmp_path / "legacy-regulation.pdf"
    regulation_path.write_bytes(b"legacy regulation bytes")
    definition = _definition()
    with engine.begin() as connection:
        connection.execute(forms.insert().values(id=1, name="Legacy"))
        connection.execute(regulations.insert().values(id=1, form_id=1, original_filename="legacy.pdf", storage_path=str(regulation_path), mime_type="application/pdf", size_bytes=regulation_path.stat().st_size, uploaded_by_user_id=None))
        connection.execute(form_versions.insert().values(id=1, form_id=1, version_major=1, version_minor=0, status="published", definition_json=definition))
        connection.execute(submissions.insert().values(id=1, form_version_id=1, accept_terms=True, data_json={"accept_terms": "Tak", "marketing": "Nie"}))
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        migration.upgrade()
        snapshot_table = Table("submission_consents", MetaData(), autoload_with=connection)
        rows = connection.execute(select(snapshot_table)).mappings().all()
        assert len(rows) == 2
        accepted = next(row for row in rows if row["consent_key"] == "accept_terms")
        assert accepted["accepted"] is True
        assert accepted["accepted_at"] is None
        assert accepted["metadata_json"]["migration_snapshot"] is True
        assert accepted["regulation_sha256"] == sha256(regulation_path.read_bytes()).hexdigest()
    assert "regulation_version_id" in {column["name"] for column in inspect(engine).get_columns("form_versions")}
