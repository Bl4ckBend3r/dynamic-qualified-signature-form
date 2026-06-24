import json

import pytest

pytest.importorskip("sqlalchemy")

from models import Base
from scripts.check_p4_schema import build_parser, main, write_report
from services.p4_schema_check_service import P4SchemaCheckService
from sqlalchemy import create_engine, text


def make_engine(tmp_path, name="schema.db"):
    return create_engine(f"sqlite:///{tmp_path / name}", future=True)


def test_schema_check_passes_for_current_models(tmp_path):
    engine = make_engine(tmp_path)
    Base.metadata.create_all(engine)

    report = P4SchemaCheckService().check_schema(engine)

    assert report["schema_ready"] is True
    assert report["missing_tables"] == []
    assert report["missing_columns"] == {}


def test_schema_check_detects_missing_original_filename_and_many_columns(tmp_path):
    engine = make_engine(tmp_path)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE submission_files (
                    id INTEGER PRIMARY KEY,
                    submission_id INTEGER,
                    public_submission_id VARCHAR(64),
                    form_slug VARCHAR(255),
                    document_id VARCHAR(128),
                    document_type VARCHAR(128),
                    filename VARCHAR(512),
                    storage_path TEXT,
                    mime_type VARCHAR(255),
                    size_bytes INTEGER,
                    signed BOOLEAN,
                    checksum_sha256 VARCHAR(64),
                    status VARCHAR(64),
                    created_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE submission_workflow_events (
                    id INTEGER PRIMARY KEY,
                    submission_id INTEGER,
                    public_submission_id VARCHAR(64),
                    form_slug VARCHAR(255),
                    previous_status VARCHAR(128),
                    new_status VARCHAR(128),
                    previous_step VARCHAR(128),
                    new_step VARCHAR(128),
                    actor_id INTEGER,
                    actor_email VARCHAR(255),
                    actor_role VARCHAR(64),
                    reason TEXT,
                    source VARCHAR(128),
                    created_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE submission_decisions (
                    id INTEGER PRIMARY KEY,
                    submission_id INTEGER,
                    public_submission_id VARCHAR(64),
                    form_slug VARCHAR(255),
                    decision VARCHAR(64),
                    justification TEXT,
                    officer_id INTEGER,
                    officer_email VARCHAR(255),
                    previous_status VARCHAR(128),
                    target_status VARCHAR(128),
                    email_requested BOOLEAN,
                    email_sent BOOLEAN,
                    email_log_id INTEGER,
                    decided_at DATETIME,
                    created_at DATETIME
                )
                """
            )
        )

    report = P4SchemaCheckService().check_schema(engine)

    assert report["schema_ready"] is False
    assert report["missing_tables"] == []
    assert "original_filename" in report["missing_columns"]["submission_files"]
    assert "signature_status" in report["missing_columns"]["submission_files"]
    assert "updated_at" in report["missing_columns"]["submission_files"]


def test_schema_check_detects_missing_table(tmp_path):
    engine = make_engine(tmp_path)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE submission_files (id INTEGER PRIMARY KEY)"))

    report = P4SchemaCheckService().check_schema(engine)

    assert report["schema_ready"] is False
    assert "submission_workflow_events" in report["missing_tables"]
    assert "submission_decisions" in report["missing_tables"]


def test_schema_check_cli_exit_codes_and_json_report(tmp_path):
    ready_url = f"sqlite:///{tmp_path / 'ready.db'}"
    engine = create_engine(ready_url, future=True)
    Base.metadata.create_all(engine)
    assert main(["--database-url", ready_url]) == 0

    missing_url = f"sqlite:///{tmp_path / 'missing.db'}"
    create_engine(missing_url, future=True)
    report_path = tmp_path / "reports" / "schema.json"
    assert main(["--database-url", missing_url, "--report", str(report_path)]) == 1
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved["schema_ready"] is False
    assert "submission_files" in saved["missing_tables"]

    args = build_parser().parse_args(["--database-url", "sqlite:///x.db", "--report", "out.json"])
    assert args.database_url == "sqlite:///x.db"
    assert args.report == "out.json"


def test_schema_check_missing_database_url_returns_technical_error(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert main([]) == 2

    captured = capsys.readouterr()
    assert "DATABASE_URL is required" in captured.err


def test_write_report_creates_parent_directory(tmp_path):
    report_path = tmp_path / "nested" / "schema.json"

    write_report({"schema_ready": True}, str(report_path))

    assert json.loads(report_path.read_text(encoding="utf-8"))["schema_ready"] is True


def test_p4_migration_contains_submission_file_alignment_columns():
    migration = open(
        "migrations/versions/20260610_0009_p4_dual_write_audit_structures.py",
        encoding="utf-8",
    ).read()

    for column in [
        "original_filename",
        "signature_status",
        "signature_validation_result",
        "agreement_number",
        "training_key",
        "generated_at",
        "signed_at",
        "updated_at",
    ]:
        assert column in migration
    assert "drop_table(\"form_submissions\"" not in migration
