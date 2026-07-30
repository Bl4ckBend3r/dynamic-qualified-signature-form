from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker

from models import Base, FormSubmission
from repositories.submission_repository import PostgresSubmissionRepository
from services.public_submission_status_service import build_public_submission_status


MIGRATION_PATH = Path(
    "migrations/versions/20260730_0028_layered_workflow_state.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("workflow_v2_migration", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _legacy_schema(connection) -> None:
    connection.exec_driver_sql(
        """
        CREATE TABLE form_submissions (
            id INTEGER PRIMARY KEY,
            process_status VARCHAR(128),
            workflow_step VARCHAR(128)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE TABLE submission_workflow_events (id INTEGER PRIMARY KEY)"
    )


def _run_upgrade(module, connection) -> None:
    module.op = Operations(MigrationContext.configure(connection))
    module.upgrade()


def test_upgrade_adds_columns_and_backfills_legacy_values():
    module = _load_migration()
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _legacy_schema(connection)
        connection.exec_driver_sql(
            """
            INSERT INTO form_submissions (id, process_status, workflow_step)
            VALUES (1, 'FORM_SUBMITTED', 'submission')
            """
        )

        _run_upgrade(module, connection)

        columns = {
            column["name"]: column
            for column in sa.inspect(connection).get_columns("form_submissions")
        }
        row = connection.execute(
            sa.text(
                """
                SELECT workflow_stage, final_outcome, document_states,
                       legacy_process_status
                FROM form_submissions
                WHERE id = 1
                """
            )
        ).mappings().one()

    assert set(columns) >= {
        "workflow_stage",
        "final_outcome",
        "document_states",
        "legacy_process_status",
    }
    assert columns["workflow_stage"]["nullable"] is True
    assert columns["final_outcome"]["nullable"] is True
    assert columns["document_states"]["nullable"] is True
    assert columns["legacy_process_status"]["nullable"] is True
    assert row["legacy_process_status"] == "FORM_SUBMITTED"
    assert row["workflow_stage"] == "submission"
    assert row["final_outcome"] is None
    assert row["document_states"] is None


def test_upgrade_is_idempotent_and_does_not_overwrite_existing_values():
    module = _load_migration()
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _legacy_schema(connection)
        _run_upgrade(module, connection)
        connection.execute(
            sa.text(
                """
                INSERT INTO form_submissions (
                    id, process_status, workflow_step, workflow_stage,
                    final_outcome, legacy_process_status
                )
                VALUES (
                    1, 'FORM_SUBMITTED', 'submission', 'custom_stage',
                    'COMPLETED', 'ORIGINAL_STATUS'
                )
                """
            )
        )

        _run_upgrade(module, connection)
        row = connection.execute(
            sa.text(
                """
                SELECT workflow_stage, final_outcome, legacy_process_status
                FROM form_submissions
                WHERE id = 1
                """
            )
        ).mappings().one()
        column_names = [
            column["name"]
            for column in sa.inspect(connection).get_columns("form_submissions")
        ]

    assert column_names.count("workflow_stage") == 1
    assert column_names.count("legacy_process_status") == 1
    assert row == {
        "workflow_stage": "custom_stage",
        "final_outcome": "COMPLETED",
        "legacy_process_status": "ORIGINAL_STATUS",
    }


def test_json_type_is_jsonb_only_for_postgresql():
    module = _load_migration()

    module.op = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    )
    assert isinstance(module._json_type(), postgresql.JSONB)

    for dialect_name in ("mysql", "mariadb"):
        module.op = SimpleNamespace(
            get_bind=lambda name=dialect_name: SimpleNamespace(
                dialect=SimpleNamespace(name=name)
            )
        )
        json_type = module._json_type()
        assert isinstance(json_type, sa.JSON)
        assert not isinstance(json_type, postgresql.JSONB)


def test_downgrade_only_drops_columns_that_exist():
    module = _load_migration()
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _legacy_schema(connection)
        _run_upgrade(module, connection)
        module.downgrade()
        module.downgrade()

        submission_columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("form_submissions")
        }
        event_columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns(
                "submission_workflow_events"
            )
        }

    assert not submission_columns & {
        "workflow_stage",
        "final_outcome",
        "document_states",
        "legacy_process_status",
    }
    assert not event_columns & {"decision_code", "user_message", "side_effects"}


def test_model_and_repository_can_read_submission_with_nullable_workflow_state():
    engine = sa.create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        session.add(
            FormSubmission(
                submission_id="migration-read-test",
                form_slug="sample",
                form_name="Sample",
                process_status="FORM_SUBMITTED",
                workflow_step="submission",
                workflow_stage=None,
                final_outcome=None,
                document_states=None,
                legacy_process_status=None,
            )
        )
        session.commit()

    repository = PostgresSubmissionRepository(
        "sqlite:///:memory:",
        session_factory=session_factory,
    )
    rows = repository.list_by_form("sample")
    public_status = build_public_submission_status(rows[0])

    assert len(rows) == 1
    assert rows[0]["workflow_stage"] == ""
    assert rows[0]["legacy_process_status"] == ""
    assert public_status["effective_process_status"] == "FORM_SUBMITTED"
    assert public_status["status_title"]
