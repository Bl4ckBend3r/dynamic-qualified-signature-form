from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

from sqlalchemy import func, inspect, select, text

from database import create_engine, create_session_factory
from models import Form, FormSubmission, FormVersion, SubmissionFile, SubmissionTraining, SubmissionWorkflowEvent
from scripts.maintenance.backup_restore import BackupError, alembic_head


TABLE_MODELS = {
    "forms": Form,
    "form_versions": FormVersion,
    "form_submissions": FormSubmission,
    "submission_trainings": SubmissionTraining,
    "submission_files": SubmissionFile,
    "submission_workflow_events": SubmissionWorkflowEvent,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def verify_integrity(database_url: str, expectations: dict, storage_root: Path) -> dict:
    engine = create_engine(database_url)
    required_tables = set(TABLE_MODELS) | {"alembic_version"}
    missing = required_tables - set(inspect(engine).get_table_names())
    if missing:
        raise BackupError(f"Missing required restored tables: {sorted(missing)}")
    session_factory = create_session_factory(database_url)
    with session_factory() as db:
        revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        if revision != alembic_head():
            raise BackupError(f"Restored schema revision {revision!r} does not match application head.")
        counts = {
            table: db.execute(select(func.count()).select_from(model)).scalar_one()
            for table, model in TABLE_MODELS.items()
        }
        for table, expected in expectations.get("exact_counts", {}).items():
            if counts.get(table) != expected:
                raise BackupError(f"Restored {table} count {counts.get(table)} does not equal {expected}.")
        orphan_versions = db.execute(
            select(func.count()).select_from(FormSubmission).outerjoin(FormVersion).where(
                FormSubmission.form_version_id.is_not(None), FormVersion.id.is_(None)
            )
        ).scalar_one()
        orphan_trainings = db.execute(
            select(func.count()).select_from(SubmissionTraining).outerjoin(
                FormSubmission, FormSubmission.id == SubmissionTraining.submission_id
            ).where(FormSubmission.id.is_(None))
        ).scalar_one()
        orphan_files = db.execute(
            select(func.count()).select_from(SubmissionFile).outerjoin(
                FormSubmission, FormSubmission.id == SubmissionFile.submission_id
            ).where(FormSubmission.id.is_(None))
        ).scalar_one()
        orphan_events = db.execute(
            select(func.count()).select_from(SubmissionWorkflowEvent).outerjoin(
                FormSubmission, FormSubmission.id == SubmissionWorkflowEvent.submission_id
            ).where(SubmissionWorkflowEvent.submission_id.is_not(None), FormSubmission.id.is_(None))
        ).scalar_one()
        if any((orphan_versions, orphan_trainings, orphan_files, orphan_events)):
            raise BackupError("Restored database contains orphaned submission relationships.")

    for document in expectations.get("documents", []):
        path = (storage_root / document["path"]).resolve()
        if storage_root.resolve() not in path.parents or not path.is_file():
            raise BackupError(f"Restored document is missing or unsafe: {document['path']}")
        if _sha256(path) != document["sha256"]:
            raise BackupError(f"Restored document checksum mismatch: {document['path']}")
    return {"alembic_revision": revision, "counts": counts, "documents": len(expectations.get("documents", []))}


def _read_revision(database_url: str) -> str | None:
    with create_engine(database_url).connect() as connection:
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()


def smoke_ready(database_url: str) -> None:
    from app import create_app
    from config import Config

    class RestoreSmokeConfig(Config):
        DATABASE_URL = database_url
        ENV = "test"
        DEBUG = False
        TESTING = True
        AUTO_CREATE_DB_SCHEMA = False
        AUTO_DB_MIGRATE = False
        SECRET_KEY = "restore-smoke-only-not-production"

    class EmptyStorage:
        @staticmethod
        def list_form_files():
            return []

    with tempfile.TemporaryDirectory(prefix="p3-restore-smoke-") as temp_dir:
        RestoreSmokeConfig.TEMP_DIR = Path(temp_dir)
        app = create_app(config_object=RestoreSmokeConfig, storage_override=EmptyStorage())
        response = app.test_client().get("/ready")
        if response.status_code != 200:
            raise BackupError(f"Restored application /ready returned {response.status_code}: {response.get_data(as_text=True)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--storage-root", type=Path, required=True)
    parser.add_argument("--expectations", type=Path, required=True)
    args = parser.parse_args()
    expectations = json.loads(args.expectations.read_text(encoding="utf-8"))
    result = verify_integrity(args.database_url, expectations, args.storage_root)
    result["alembic_revision"] = _read_revision(args.database_url)
    if result["alembic_revision"] != alembic_head():
        raise BackupError("Restored Alembic revision does not match the application head.")
    smoke_ready(args.database_url)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
