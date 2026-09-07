from __future__ import annotations

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from models import Form, FormPermission, FormSubmission, User
from services.submission_assignment_service import SubmissionAssignmentService


ROOT = os.path.dirname(os.path.dirname(__file__))
P0_TABLES = {
    "form_versions", "consent_definitions", "consent_versions",
    "form_regulation_versions", "form_version_consents", "submission_consents",
    "form_drafts",
    "submission_assignment_history", "form_assignment_states",
}
ATTACHMENT_COLUMNS = {
    "field_key", "attachment_version", "category", "uploaded_by_source",
    "workflow_step_at_upload", "antivirus_status", "rejection_reason",
    "mime_type", "size_bytes", "checksum_sha256", "status",
}


def _config(url: str) -> Config:
    config = Config(os.path.join(ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(ROOT, "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _database_url(admin_url: str, database: str) -> str:
    return make_url(admin_url).set(database=database).render_as_string(hide_password=False)


def _create_database(admin_url: str, database: str) -> None:
    assert re.fullmatch(r"p0_test_[a-f0-9]{12}", database)
    url = make_url(admin_url)
    dialect = url.get_backend_name()
    server_database = "postgres" if dialect == "postgresql" else None
    server_url = url.set(database=server_database)
    engine = sa.create_engine(server_url, isolation_level="AUTOCOMMIT")
    quote = engine.dialect.identifier_preparer.quote_identifier(database)
    with engine.connect() as connection:
        connection.exec_driver_sql(f"CREATE DATABASE {quote}")
    engine.dispose()


def _drop_database(admin_url: str, database: str) -> None:
    assert re.fullmatch(r"p0_test_[a-f0-9]{12}", database)
    url = make_url(admin_url)
    dialect = url.get_backend_name()
    server_database = "postgres" if dialect == "postgresql" else None
    engine = sa.create_engine(url.set(database=server_database), isolation_level="AUTOCOMMIT")
    quote = engine.dialect.identifier_preparer.quote_identifier(database)
    with engine.connect() as connection:
        if dialect == "postgresql":
            connection.execute(
                sa.text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name AND pid <> pg_backend_pid()"),
                {"name": database},
            )
        connection.exec_driver_sql(f"DROP DATABASE {quote}")
    engine.dispose()


def _json_value(engine, value):
    return json.dumps(value, ensure_ascii=False) if engine.dialect.name in {"mysql", "mariadb"} else value


def _complete_required(table: sa.Table, values: dict) -> dict:
    result = dict(values)
    for column in table.columns:
        if column.name in result or column.primary_key or column.nullable or column.default is not None or column.server_default is not None:
            continue
        if isinstance(column.type, sa.Boolean):
            result[column.name] = False
        elif isinstance(column.type, sa.Integer):
            result[column.name] = 0
        elif isinstance(column.type, sa.DateTime):
            result[column.name] = datetime.now(timezone.utc)
        elif isinstance(column.type, sa.JSON):
            result[column.name] = {}
        else:
            result[column.name] = ""
    return result


def _seed_pre_p0(engine) -> tuple[list[int], list[int]]:
    metadata = sa.MetaData()
    forms = sa.Table("forms", metadata, autoload_with=engine)
    fields = sa.Table("form_fields", metadata, autoload_with=engine)
    submissions = sa.Table("form_submissions", metadata, autoload_with=engine)
    files = sa.Table("submission_files", metadata, autoload_with=engine)
    regulations = sa.Table("form_regulations", metadata, autoload_with=engine)
    form_ids, submission_ids = [], []
    with engine.begin() as connection:
        for form_index in range(2):
            slug = f"legacy-{form_index}"
            definition = {
                "title": f"Legacy {form_index}",
                "fields": [
                    {"name": "email", "label": "Email", "type": "email", "required": True},
                    {
                        "name": "terms", "label": "Accept terms", "type": "checkbox",
                        "required": True, "consent_type": "regulation", "pdf_text": "Accept terms",
                    },
                ],
                "workflow": {"initial_step": "submission", "steps": [{"id": "submission"}]},
            }
            form_row = _complete_required(forms, {
                "slug": slug, "name": f"Legacy {form_index}", "title": f"Legacy {form_index}",
                "definition_json": _json_value(engine, definition), "is_active": True, "is_public": True,
            })
            form_id = connection.execute(forms.insert().values(**form_row)).inserted_primary_key[0]
            form_ids.append(int(form_id))
            field_row = _complete_required(fields, {
                "form_id": form_id, "name": "email", "label": "Email", "type": "email", "required": True,
                "options": _json_value(engine, []), "default_value": "", "section": "", "stage": "initial_submission",
                "sort_order": 1, "active": True,
            })
            connection.execute(fields.insert().values(**field_row))
            consent_field = _complete_required(fields, {
                "form_id": form_id, "name": "terms", "label": "Accept terms", "type": "checkbox",
                "required": True, "options": _json_value(engine, []), "default_value": "",
                "section": "", "stage": "initial_submission", "sort_order": 2, "active": True,
            })
            connection.execute(fields.insert().values(**consent_field))
            regulation_row = _complete_required(regulations, {
                "form_id": form_id,
                "original_filename": "integration-regulation.txt",
                "storage_path": os.path.abspath(__file__),
                "mime_type": "text/plain",
                "size_bytes": os.path.getsize(__file__),
            })
            connection.execute(regulations.insert().values(**regulation_row))
            for submission_index in range(3):
                public_id = f"legacy-{form_index}-{submission_index}"
                submission_row = _complete_required(submissions, {
                    "submission_id": public_id, "form_slug": slug, "form_name": f"Legacy {form_index}",
                    "email": f"user{submission_index}@example.test",
                    "data_json": _json_value(engine, {"email": f"user{submission_index}@example.test", "terms": True}),
                    "process_status": "submitted" if submission_index % 2 == 0 else "waiting_for_review",
                })
                submission_id = connection.execute(submissions.insert().values(**submission_row)).inserted_primary_key[0]
                submission_ids.append(int(submission_id))
                if submission_index == 0:
                    file_row = _complete_required(files, {
                        "submission_id": submission_id,
                        "public_submission_id": public_id,
                        "filename": f"legacy-{form_index}.pdf",
                        "storage_path": f"legacy/{form_index}.pdf",
                        "document_type": "application_pdf",
                    })
                    connection.execute(files.insert().values(**file_row))
    return form_ids, submission_ids


def _assert_p0_schema_and_backfill(engine, form_ids=(), submission_ids=()) -> None:
    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names())
    assert P0_TABLES <= tables
    assert {"form_version_id"} <= {item["name"] for item in inspector.get_columns("form_submissions")}
    assert ATTACHMENT_COLUMNS <= {item["name"] for item in inspector.get_columns("submission_files")}
    assert {"is_listed", "share_token_hash"} <= {item["name"] for item in inspector.get_columns("forms")}
    assert "availability_json" in {item["name"] for item in inspector.get_columns("form_fields")}
    response_columns = {item["name"] for item in inspector.get_columns("training_survey_responses")}
    assert {
        "attempt_number", "status", "started_at", "completed_at", "score_points",
        "max_points", "score_percent", "test_snapshot_json",
    } <= response_columns
    response_indexes = inspector.get_indexes("training_survey_responses")
    assert any(not item.get("unique") and item["column_names"] == ["invitation_id"] for item in response_indexes)
    response_uniques = inspector.get_unique_constraints("training_survey_responses")
    assert not any(item["column_names"] == ["invitation_id"] for item in response_uniques)
    assert any(item["column_names"] == ["invitation_id", "attempt_number"] for item in response_uniques)
    response_foreign_keys = inspector.get_foreign_keys("training_survey_responses")
    assert any(
        item["constrained_columns"] == ["invitation_id"]
        and item["referred_table"] == "training_survey_invitations"
        and item["referred_columns"] == ["id"]
        for item in response_foreign_keys
    )
    metadata = sa.MetaData()
    versions = sa.Table("form_versions", metadata, autoload_with=engine)
    submissions = sa.Table("form_submissions", metadata, autoload_with=engine)
    consent_definitions = sa.Table("consent_definitions", metadata, autoload_with=engine)
    consent_versions = sa.Table("consent_versions", metadata, autoload_with=engine)
    version_consents = sa.Table("form_version_consents", metadata, autoload_with=engine)
    submission_consents = sa.Table("submission_consents", metadata, autoload_with=engine)
    drafts = sa.Table("form_drafts", metadata, autoload_with=engine)
    files = sa.Table("submission_files", metadata, autoload_with=engine)
    with engine.connect() as connection:
        expected_head = ScriptDirectory.from_config(_config(str(engine.url))).get_current_head()
        assert MigrationContext.configure(connection).get_current_revision() == expected_head
        assert connection.scalar(sa.text("SELECT COUNT(*) FROM alembic_version")) == 1
        for form_id in form_ids:
            assert connection.scalar(sa.select(sa.func.count()).select_from(versions).where(versions.c.form_id == form_id)) >= 1
        for submission_id in submission_ids:
            version_id = connection.scalar(sa.select(submissions.c.form_version_id).where(submissions.c.id == submission_id))
            assert version_id is not None
            assert connection.scalar(sa.select(sa.func.count()).select_from(versions).where(versions.c.id == version_id)) == 1
        if form_ids:
            assert connection.scalar(sa.select(sa.func.count()).select_from(consent_definitions)) >= len(form_ids)
            assert connection.scalar(sa.select(sa.func.count()).select_from(consent_versions)) >= len(form_ids)
            assert connection.scalar(sa.select(sa.func.count()).select_from(submission_consents)) >= len(submission_ids)

        relationships = (
            (submissions, submissions.c.form_version_id, versions, versions.c.id),
            (consent_versions, consent_versions.c.consent_definition_id, consent_definitions, consent_definitions.c.id),
            (version_consents, version_consents.c.form_version_id, versions, versions.c.id),
            (version_consents, version_consents.c.consent_version_id, consent_versions, consent_versions.c.id),
            (submission_consents, submission_consents.c.submission_id, submissions, submissions.c.id),
            (submission_consents, submission_consents.c.consent_version_id, consent_versions, consent_versions.c.id),
            (drafts, drafts.c.form_version_id, versions, versions.c.id),
            (drafts, drafts.c.submission_id, submissions, submissions.c.id),
            (files, files.c.submission_id, submissions, submissions.c.id),
        )
        for child, child_fk, parent, parent_pk in relationships:
            orphan_count = connection.scalar(
                sa.select(sa.func.count()).select_from(
                    child.outerjoin(parent, child_fk == parent_pk)
                ).where(child_fk.is_not(None), parent_pk.is_(None))
            )
            assert orphan_count == 0, f"Unexpected orphan: {child.name}.{child_fk.name}"

        json_type = str(next(item["type"] for item in inspector.get_columns("form_versions") if item["name"] == "definition_json")).upper()
        if engine.dialect.name == "postgresql":
            assert "JSONB" in json_type
        else:
            assert "JSON" in json_type or "TEXT" in json_type

    attachment_indexes = {item["name"] for item in inspector.get_indexes("submission_files")}
    assert "ix_submission_files_attachment_field" in attachment_indexes
    draft_indexes = {item["name"] for item in inspector.get_indexes("form_drafts")}
    assert {"ix_form_drafts_token_hash", "ix_form_drafts_status_expires"} <= draft_indexes


@pytest.mark.parametrize(
    "environment_name",
    ["P0_MARIADB_ADMIN_DATABASE_URL", "P0_POSTGRES_ADMIN_DATABASE_URL"],
)
def test_p0_clean_and_pre_p0_upgrade(environment_name):
    admin_url = os.getenv(environment_name, "").strip()
    if not admin_url:
        pytest.skip(f"Set {environment_name} to run this database integration test.")
    for scenario in ("clean", "pre_p0"):
        database = f"p0_test_{uuid4().hex[:12]}"
        _create_database(admin_url, database)
        url = _database_url(admin_url, database)
        engine = sa.create_engine(url)
        try:
            config = _config(url)
            if scenario == "clean":
                command.upgrade(config, "head")
                _assert_p0_schema_and_backfill(engine)
            else:
                command.upgrade(config, "20260804_0030")
                form_ids, submission_ids = _seed_pre_p0(engine)
                command.upgrade(config, "head")
                _assert_p0_schema_and_backfill(engine, form_ids, submission_ids)
            command.downgrade(config, "-1")
            command.upgrade(config, "head")
            _assert_p0_schema_and_backfill(engine)
        finally:
            engine.dispose()
            _drop_database(admin_url, database)


def test_training_0047_retries_partial_mariadb_ddl():
    admin_url = os.getenv("P0_MARIADB_ADMIN_DATABASE_URL", "").strip()
    if not admin_url:
        pytest.skip("Set P0_MARIADB_ADMIN_DATABASE_URL to run this MariaDB migration retry test.")
    database = f"p0_test_{uuid4().hex[:12]}"
    _create_database(admin_url, database)
    url = _database_url(admin_url, database)
    engine = sa.create_engine(url)
    try:
        config = _config(url)
        command.upgrade(config, "20260826_0046")
        with engine.begin() as connection:
            operations = Operations(MigrationContext.configure(connection))
            operations.add_column("training_surveys", sa.Column("survey_type", sa.String(16), nullable=False, server_default="survey"))
            operations.add_column("training_surveys", sa.Column("attempt_policy", sa.String(24), nullable=False, server_default="single_attempt"))
            operations.add_column("training_surveys", sa.Column("post_requires_attendance", sa.Boolean(), nullable=False, server_default=sa.false()))
            operations.create_check_constraint("ck_training_surveys_type", "training_surveys", "survey_type IN ('survey', 'pre_test', 'post_test')")
            operations.create_check_constraint("ck_training_surveys_attempt_policy", "training_surveys", "attempt_policy IN ('single_attempt', 'multiple_attempts')")
            operations.create_index("ix_training_surveys_training_type", "training_surveys", ["form_id", "training_id", "survey_type", "status"])
            operations.drop_constraint("ck_training_survey_questions_type", "training_survey_questions", type_="check")
            operations.add_column("training_survey_questions", sa.Column("is_scored", sa.Boolean(), nullable=False, server_default=sa.false()))
            operations.add_column("training_survey_questions", sa.Column("points", sa.Numeric(10, 2), nullable=False, server_default="0"))
            operations.add_column("training_survey_questions", sa.Column("correct_answers_json", sa.JSON(), nullable=False, server_default="[]"))
            operations.add_column("training_survey_questions", sa.Column("comparison_key", sa.String(128), nullable=False, server_default=""))
            operations.create_check_constraint("ck_training_survey_questions_type", "training_survey_questions", "question_type IN ('scale', 'single_choice', 'multiple_choice', 'true_false', 'text')")
        command.upgrade(config, "head")
        _assert_p0_schema_and_backfill(engine)
    finally:
        engine.dispose()
        _drop_database(admin_url, database)


@pytest.mark.parametrize(
    "environment_name",
    ["P0_MARIADB_ADMIN_DATABASE_URL", "P0_POSTGRES_ADMIN_DATABASE_URL"],
)
def test_round_robin_is_serialized_on_real_database(environment_name):
    admin_url = os.getenv(environment_name, "").strip()
    if not admin_url:
        pytest.skip(f"Set {environment_name} to run this database integration test.")
    database = f"p0_test_{uuid4().hex[:12]}"
    _create_database(admin_url, database)
    url = _database_url(admin_url, database)
    engine = sa.create_engine(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        command.upgrade(_config(url), "head")
        with factory() as db:
            form = Form(slug="concurrent", name="Concurrent", definition_json={})
            users = [User(email=f"rr-{index}@example.test", password_hash="x", role="form_manager") for index in range(3)]
            db.add_all([form, *users])
            db.flush()
            db.add_all([
                FormPermission(
                    user_id=user.id, form_id=form.id, can_manage=False,
                    can_assign_submissions=True,
                )
                for user in users
            ])
            submissions = [
                FormSubmission(submission_id=f"rr-case-{index}", form_slug=form.slug, form_name=form.name)
                for index in range(9)
            ]
            db.add_all(submissions)
            db.commit()
            user_ids = [user.id for user in users]
            public_ids = [item.submission_id for item in submissions]
        service = SubmissionAssignmentService()
        config = {"assignment": {"mode": "round_robin", "eligible_users": user_ids}}
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(
                lambda public_id: service.auto_assign_created(factory, submission_id=public_id, form_config=config),
                public_ids,
            ))
        assert set(results) == set(user_ids)
        with factory() as db:
            counts = dict(db.execute(
                sa.select(FormSubmission.assigned_to_user_id, sa.func.count(FormSubmission.id))
                .group_by(FormSubmission.assigned_to_user_id)
            ).all())
            assert counts == {user_id: 3 for user_id in user_ids}
    finally:
        engine.dispose()
        _drop_database(admin_url, database)
