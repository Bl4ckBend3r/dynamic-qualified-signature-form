"""Run the existing document rename migration at normal application startup."""

import json
import os


_TRUE = {"1", "true", "yes", "tak", "on"}


def _enabled(app):
    # Production starts opt in by default. Local runs must explicitly opt in.
    production = str(app.config.get("ENV") or os.getenv("FLASK_ENV", "")).strip().lower() == "production"
    value = os.getenv("AUTO_RENAME_UPLOADED_DOCUMENTS")
    if value is None:
        value = app.config.get("AUTO_RENAME_UPLOADED_DOCUMENTS", production)
    return str(value).strip().lower() in _TRUE


def run_startup_document_rename(app, container, readiness_check):
    """Synchronous, idempotent migration using the application's real services.

    No schema changes, fallback storage writes, or unverified TLS. A failed
    migration is logged; it must not make the entire application unavailable.
    The existing database advisory lock serializes migration processes.
    """
    log = app.logger
    if app.config.get("TESTING") or not _enabled(app):
        log.info("document_rename_startup_skipped reason=disabled_or_testing")
        return

    try:
        from sqlalchemy import inspect
        from models import UploadedDocumentRename
        from services.nextcloud_storage import NextcloudStorage
        from scripts.rename_uploaded_documents import (
            RenameError, UploadedDocumentRenamer, _automatic_migration,
        )

        storage = container.storage
        if not isinstance(storage, NextcloudStorage):
            log.warning("document_rename_startup_skipped reason=not_direct_nextcloud")
            return
        if not getattr(storage, "verify_ssl", False):
            log.error("document_rename_startup_skipped reason=TLS_VERIFICATION_DISABLED")
            return
        if readiness_check(app.config.get("DATABASE_URL"))["status"] != "ready":
            log.error("document_rename_startup_skipped reason=DATABASE_NOT_READY")
            return

        # Use the exact session factory and storage used by the application.
        sessions = container.submission_repository.session_factory
        engine = sessions.kw["bind"]
        if engine.dialect.name not in {"mysql", "mariadb", "postgresql"}:
            log.warning("document_rename_startup_skipped reason=UNSUPPORTED_DATABASE")
            return
        table = UploadedDocumentRename.__tablename__
        inspector = inspect(engine)
        if not inspector.has_table(table):
            log.error("document_rename_startup_skipped reason=MISSING_JOURNAL")
            return
        expected = {column.name for column in UploadedDocumentRename.__table__.columns}
        actual = {column["name"] for column in inspector.get_columns(table)}
        if not expected.issubset(actual):
            log.error("document_rename_startup_skipped reason=INCOMPLETE_JOURNAL_SCHEMA")
            return

        log.info("document_rename_startup_started")
        try:
            report = _automatic_migration(UploadedDocumentRenamer(sessions, storage))
        except RenameError as exc:
            if exc.code == "MIGRATION_LOCKED":
                log.info("document_rename_startup_skipped reason=MIGRATION_LOCKED")
                return
            log.error("document_rename_startup_failed reason=%s", exc.code)
            return
        # The existing report contains identifiers and reason codes, not PDF names.
        if report.get("errors"):
            log.error("document_rename_startup_failed report=%s", json.dumps(report, ensure_ascii=True))
        else:
            log.info("document_rename_startup_completed report=%s", json.dumps(report, ensure_ascii=True))
    except Exception as exc:
        # Do not put URLs, credentials, paths or participant data into startup logs.
        log.error("document_rename_startup_failed reason=%s", type(exc).__name__)
