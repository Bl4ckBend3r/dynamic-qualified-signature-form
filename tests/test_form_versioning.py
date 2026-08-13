from __future__ import annotations

from copy import deepcopy
import importlib
import re
from types import SimpleNamespace

from alembic.migration import MigrationContext
from alembic.operations import Operations
from flask import Blueprint, Flask
from sqlalchemy import Column, Integer, JSON, MetaData, String, Table, Text, create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

from models import Base, Form, FormField, FormSubmission, FormVersion
from routes import documents as document_routes
from routes import public_forms
from services.form_config_service import FormConfigService
from services.form_version_service import (
    FORM_VERSION_ARCHIVED,
    FORM_VERSION_DRAFT,
    FORM_VERSION_PUBLISHED,
    FormVersionError,
    FormVersionService,
)


def _definition(field_name: str = "email", title: str = "Formularz") -> dict:
    return {
        "title": title,
        "fields": [
            {
                "name": field_name,
                "label": field_name.title(),
                "type": "email" if field_name == "email" else "text",
                "required": True,
            }
        ],
        "workflow": {"initial_step": "submission", "steps": [{"id": "submission"}]},
    }


def _database(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'versions.db'}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    return database_url, sessionmaker(bind=engine, expire_on_commit=False)


def _form_with_field(db, *, slug="versioned") -> Form:
    form = Form(
        slug=slug,
        name="Formularz",
        title="Formularz",
        description="",
        definition_json=_definition(),
        is_active=True,
        is_public=True,
    )
    db.add(form)
    db.flush()
    db.add(
        FormField(
            form_id=form.id,
            name="email",
            label="Email",
            type="email",
            required=True,
            options=[],
            default_value="",
            section="",
            stage="initial_submission",
            sort_order=1,
            active=True,
        )
    )
    db.flush()
    return form


def test_publish_archives_previous_and_clone_preserves_snapshot(tmp_path):
    _url, Session = _database(tmp_path)
    service = FormVersionService()
    with Session.begin() as db:
        form = _form_with_field(db)
        first = service.create_initial_version(db, form, actor_id=None, status=FORM_VERSION_DRAFT)
        service.publish(db, form, first, actor_id=None, change_summary="Pierwsza publikacja")
        first_id = first.id

    with Session.begin() as db:
        form = db.execute(select(Form).where(Form.slug == "versioned")).scalar_one()
        first = db.get(FormVersion, first_id)
        draft = service.clone_to_draft(db, form, first, actor_id=None, bump="minor")
        copied = deepcopy(draft.definition_json)
        copied["title"] = "Formularz 1.1"
        copied["fields"][0]["label"] = "Nowy e-mail"
        service.update_definition(draft, copied, change_summary="Zmiana etykiety")
        service.publish(db, form, draft, actor_id=None)
        draft_id = draft.id

    with Session() as db:
        first = db.get(FormVersion, first_id)
        second = db.get(FormVersion, draft_id)
        assert first.status == FORM_VERSION_ARCHIVED
        assert first.definition_json["title"] == "Formularz"
        assert second.status == FORM_VERSION_PUBLISHED
        assert second.version_label == "1.1"
        assert second.source_version_id == first.id
        assert service.resolve_published(db, first.form_id).id == second.id


def test_archived_and_published_versions_are_immutable(tmp_path):
    _url, Session = _database(tmp_path)
    service = FormVersionService()
    with Session.begin() as db:
        form = _form_with_field(db)
        version = service.create_initial_version(db, form, actor_id=None, status=FORM_VERSION_PUBLISHED)
        try:
            service.update_definition(version, _definition("name"))
        except FormVersionError as exc:
            assert "roboczą" in str(exc)
        else:
            raise AssertionError("Opublikowana wersja została zmieniona")

        service.archive(db, version)
        try:
            service.update_definition(version, _definition("name"))
        except FormVersionError as exc:
            assert "roboczą" in str(exc)
        else:
            raise AssertionError("Archiwalna wersja została zmieniona")


def test_clone_uses_unambiguous_minor_and_major_numbers(tmp_path):
    _url, Session = _database(tmp_path)
    service = FormVersionService()
    with Session.begin() as db:
        form = _form_with_field(db)
        first = service.create_initial_version(db, form, actor_id=None, status=FORM_VERSION_PUBLISHED)
        minor = service.clone_to_draft(db, form, first, actor_id=None, bump="minor")
        assert (minor.version_major, minor.version_minor, minor.version_label) == (1, 1, "1.1")
        service.publish(db, form, minor, actor_id=None)
        major = service.clone_to_draft(db, form, minor, actor_id=None, bump="major")
        assert (major.version_major, major.version_minor, major.version_label) == (2, 0, "2.0")


def test_publish_rejects_label_inconsistent_with_numeric_version(tmp_path):
    _url, Session = _database(tmp_path)
    service = FormVersionService()
    with Session.begin() as db:
        form = _form_with_field(db)
        draft = service.create_initial_version(db, form, actor_id=None, status=FORM_VERSION_DRAFT)
        draft.version_label = "1.10"
        try:
            service.publish(db, form, draft, actor_id=None)
        except FormVersionError as exc:
            assert "major/minor" in str(exc)
        else:
            raise AssertionError("Opublikowano wersję z niespójną etykietą")


def test_submission_relationship_points_to_exact_version(tmp_path):
    _url, Session = _database(tmp_path)
    service = FormVersionService()
    with Session.begin() as db:
        form = _form_with_field(db)
        version = service.create_initial_version(db, form, actor_id=None, status=FORM_VERSION_PUBLISHED)
        submission = FormSubmission(
            submission_id="public-1",
            form_slug=form.slug,
            form_name=form.name,
            form_version_id=version.id,
            data_json={},
            access_token="token",
        )
        db.add(submission)
        db.flush()
        submission_id = submission.id
        version_id = version.id

    with Session() as db:
        submission = db.get(FormSubmission, submission_id)
        assert submission.form_version.id == version_id
        assert service.resolve_for_submission(db, submission).definition_json["title"] == "Formularz"


def _public_app(monkeypatch, Session, calls):
    app = Flask(__name__, template_folder=str(public_forms.Path(__file__).resolve().parents[1] / "templates"))
    app.config.update(TESTING=True, SECRET_KEY="test-secret", DATABASE_URL="sqlite:///test", WTF_CSRF_ENABLED=False)
    app.jinja_env.filters["trusted_html"] = lambda value: value
    app.jinja_env.filters["option_value"] = lambda value: value.get("value", "") if isinstance(value, dict) else value
    app.jinja_env.filters["option_label"] = lambda value: value.get("label", "") if isinstance(value, dict) else value

    class SubmissionServiceStub:
        def submit_form(self, slug, definition, payload, *, form_version_id=None):
            calls.append({"slug": slug, "definition": definition, "form_version_id": form_version_id})
            return {
                "ok": True,
                "errors": {},
                "values": {},
                "result": {
                    "submission_id": "new-submission",
                    "form_slug": slug,
                    "form_title": definition["title"],
                    "pdf_filename": "",
                    "pdf_url": "",
                    "signed_pdf_filename": "",
                    "signed_pdf_url": None,
                    "upload_url": "#",
                    "signature_status": "manual",
                    "verification": None,
                },
            }

    app.extensions["services"] = SimpleNamespace(
        form_version_service=FormVersionService(),
        submission_service=SubmissionServiceStub(),
    )
    monkeypatch.setattr(public_forms, "db_session_factory", lambda: Session)
    documents = Blueprint("documents", __name__)
    documents.add_url_rule("/documents-to-sign", "documents_to_sign", lambda: "")
    app.register_blueprint(documents)
    app.register_blueprint(public_forms.bp)
    @app.context_processor
    def _layout_context():
        return {
            "app_name": "Test",
            "app_base_path": "",
            "site_footer": SimpleNamespace(
                logo_position="top",
                layout="single",
                logo_html="",
                left_html="",
                right_html="",
                social_left_html="",
                social_right_html="",
                social_bottom_html="",
            ),
            "footer_contact": SimpleNamespace(address="", phones=[], email=""),
            "footer_service_documents": [],
        }
    return app


def test_public_get_requires_published_and_post_stays_on_rendered_version(tmp_path, monkeypatch):
    _url, Session = _database(tmp_path)
    service = FormVersionService()
    with Session.begin() as db:
        form = _form_with_field(db, slug="public-version")
        first = service.create_initial_version(db, form, actor_id=None, status=FORM_VERSION_PUBLISHED)
        first_id = first.id

    calls = []
    app = _public_app(monkeypatch, Session, calls)
    client = app.test_client()
    response = client.get("/form/public-version")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    token = re.search(r'name="form_version_token" value="([^"]+)"', html).group(1)

    with Session.begin() as db:
        form = db.execute(select(Form).where(Form.slug == "public-version")).scalar_one()
        first = db.get(FormVersion, first_id)
        second = service.clone_to_draft(db, form, first, actor_id=None, bump="minor")
        updated = deepcopy(second.definition_json)
        updated["title"] = "Nowszy formularz"
        service.update_definition(second, updated)
        service.publish(db, form, second, actor_id=None, change_summary="Nowa publikacja")

    response = client.post(
        "/submit/public-version",
        data={"form_version_token": token, "form_version_id": str(first_id), "email": "jan@example.test"},
    )
    assert response.status_code == 200
    assert calls[-1]["form_version_id"] == first_id
    assert calls[-1]["definition"]["title"] == "Formularz"

    with Session.begin() as db:
        second = service.resolve_published(db, db.execute(select(Form.id).where(Form.slug == "public-version")).scalar_one())
        service.archive(db, second)
    assert client.get("/form/public-version").status_code == 404


def test_public_post_rejects_missing_or_tampered_version_token(tmp_path, monkeypatch):
    _url, Session = _database(tmp_path)
    with Session.begin() as db:
        form = _form_with_field(db, slug="secured-version")
        FormVersionService().create_initial_version(db, form, actor_id=None, status=FORM_VERSION_PUBLISHED)
    calls = []
    client = _public_app(monkeypatch, Session, calls).test_client()
    assert client.post("/submit/secured-version", data={"email": "jan@example.test"}).status_code == 409
    assert client.post(
        "/submit/secured-version",
        data={"email": "jan@example.test", "form_version_token": "tampered"},
    ).status_code == 409
    assert calls == []


def test_document_context_uses_submission_version_after_new_publication(tmp_path, monkeypatch):
    database_url, Session = _database(tmp_path)
    service = FormVersionService()
    with Session.begin() as db:
        form = _form_with_field(db, slug="historical-doc")
        first = service.create_initial_version(db, form, actor_id=None, status=FORM_VERSION_PUBLISHED)
        submission = FormSubmission(
            submission_id="historical-submission",
            form_slug=form.slug,
            form_name=form.name,
            form_version_id=first.id,
            data_json={},
            access_token="token",
        )
        db.add(submission)
        second = service.clone_to_draft(db, form, first, actor_id=None, bump="major")
        updated = deepcopy(second.definition_json)
        updated["title"] = "Nowy dokument"
        service.update_definition(second, updated)
        service.publish(db, form, second, actor_id=None, change_summary="Nowa treść dokumentów")

    app = Flask(__name__)
    app.config.update(DATABASE_URL=database_url)
    app.extensions["services"] = SimpleNamespace(
        form_version_service=service,
        form_config_service=FormConfigService(),
    )
    monkeypatch.setattr(document_routes, "create_session_factory", lambda _url: Session)
    with app.app_context():
        historical_config = document_routes.get_form_config("historical-doc", "historical-submission")
        current_config = document_routes.get_form_config("historical-doc")

    assert historical_config["title"] == "Formularz"
    assert current_config["title"] == "Nowy dokument"


def test_migration_creates_technical_version_and_backfills_submissions(monkeypatch):
    migration = importlib.import_module("migrations.versions.20260812_0031_form_versioning")
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table("users", metadata, Column("id", Integer, primary_key=True))
    forms = Table(
        "forms",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("slug", String(255), nullable=False),
        Column("name", String(255), nullable=False),
        Column("title", String(255)),
        Column("description", Text),
        Column("definition_json", JSON),
        Column("created_by_id", Integer),
    )
    fields = Table(
        "form_fields",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("form_id", Integer, nullable=False),
        Column("name", String(255)),
        Column("label", Text),
        Column("type", String(64)),
        Column("required", Integer),
        Column("options", JSON),
        Column("default_value", Text),
        Column("section", String(255)),
        Column("sort_order", Integer),
    )
    submissions = Table(
        "form_submissions",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("form_slug", String(255), nullable=False),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(forms.insert().values(id=1, slug="legacy", name="Legacy", title="Legacy", description="", definition_json=_definition(), created_by_id=None))
        connection.execute(fields.insert().values(id=1, form_id=1, name="email", label="E-mail", type="email", required=1, options=[], default_value="", section="Kontakt", sort_order=1))
        connection.execute(submissions.insert().values(id=1, form_slug="legacy"))
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        migration.upgrade()
        versions = Table("form_versions", MetaData(), autoload_with=connection)
        migrated_submissions = Table("form_submissions", MetaData(), autoload_with=connection)
        version_rows = connection.execute(select(versions)).mappings().all()
        submission_row = connection.execute(select(migrated_submissions)).mappings().one()

    assert "form_version_id" in {column["name"] for column in inspect(engine).get_columns("form_submissions")}
    assert len(version_rows) == 1
    assert version_rows[0]["status"] == FORM_VERSION_PUBLISHED
    assert version_rows[0]["version_label"] == "1.0"
    assert version_rows[0]["definition_json"]["fields"][1]["name"] == "email"
    assert "Migracja techniczna" in version_rows[0]["change_summary"]
    assert submission_row["form_version_id"] == version_rows[0]["id"]
