from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import sqlalchemy as sa
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Engine, make_url

from database import create_engine, normalize_database_url


logger = logging.getLogger(__name__)

REQUIRED_SCHEMA_COLUMNS: dict[str, frozenset[str]] = {
    "forms": frozenset({"user_instruction", "user_instruction_config", "is_listed", "share_token_hash"}),
    "form_versions": frozenset(
        {
            "form_id",
            "version_major",
            "version_minor",
            "version_label",
            "status",
            "definition_json",
            "created_by_id",
            "published_at",
            "published_by_id",
            "archived_at",
            "change_summary",
            "source_version_id",
            "regulation_version_id",
        }
    ),
    "consent_definitions": frozenset({"form_id", "consent_key", "consent_type"}),
    "consent_versions": frozenset(
        {"consent_definition_id", "version_major", "version_minor", "consent_type", "sha256", "status", "text_snapshot"}
    ),
    "form_regulation_versions": frozenset(
        {"regulation_id", "form_id", "version_major", "version_minor", "sha256", "status", "storage_path"}
    ),
    "form_version_consents": frozenset({"form_version_id", "consent_version_id", "sort_order"}),
    "submission_consents": frozenset(
        {"submission_id", "consent_key", "consent_version", "consent_sha256", "accepted", "accepted_at"}
    ),
    "form_submissions": frozenset(
        {"form_version_id", "assigned_to_user_id", "assigned_at", "assigned_by_user_id", "priority", "due_at"}
    ),
    "submission_assignment_history": frozenset(
        {"submission_id", "assigned_to_user_id", "assigned_by_user_id", "previous_user_id", "assigned_at", "unassigned_at", "reason", "source"}
    ),
    "form_assignment_states": frozenset({"form_id", "last_assigned_user_id", "updated_at"}),
    "form_permissions": frozenset({"can_manage", "can_review", "can_assign_submissions"}),
    "form_fields": frozenset({"availability_json"}),
    "form_drafts": frozenset(
        {"public_id", "form_id", "form_version_id", "email", "data_json", "status", "token_hash", "expires_at", "completed_at", "last_autosave_at", "submission_public_id"}
    ),
    "submission_files": frozenset(
        {
            "field_key",
            "attachment_version",
            "category",
            "uploaded_by_source",
            "workflow_step_at_upload",
            "antivirus_status",
            "rejection_reason",
        }
    ),
    "email_logs": frozenset({"event_type", "error_type", "administrator_message", "html_body", "text_body"}),
    "site_footers": frozenset(
        {
            "left_html",
            "right_html",
            "layout",
            "social_links",
            "social_position",
            "social_icon_style",
            "social_show_labels",
        }
    ),
    "mail_footers": frozenset(
        {
            "logo_alignment",
            "contact_html",
            "links",
            "legal_text",
            "use_global",
            "logo_width",
            "logo_height",
            "logo_position",
            "is_default",
            "is_active",
        }
    ),
}

MIGRATION_HINT = "Database schema is behind Alembic head. Run: alembic upgrade head"


def database_migration_status(
    database_url: str | None = None,
    *,
    engine: Engine | None = None,
    project_root: Path | None = None,
) -> dict:
    if engine is None and not str(database_url or "").strip():
        raise RuntimeError("DATABASE_URL is required to inspect Alembic status.")
    checked_engine = engine or create_engine(str(database_url))
    root = project_root or Path(__file__).resolve().parents[1]
    config = AlembicConfig(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    script = ScriptDirectory.from_config(config)
    heads = tuple(script.get_heads())
    with checked_engine.connect() as connection:
        current = tuple(MigrationContext.configure(connection).get_current_heads())
    known = {revision.revision for revision in script.walk_revisions()}
    unknown = tuple(sorted(set(current) - known))
    if unknown:
        state = "unknown"
    elif len(heads) != 1:
        state = "multiple_heads"
    elif current == heads:
        state = "head"
    elif not current:
        state = "unversioned"
    else:
        state = "behind"
    return {
        "state": state,
        "current": list(current),
        "heads": list(heads),
        "head_count": len(heads),
        "unknown": list(unknown),
    }


def redact_database_url(database_url: str) -> str:
    value = str(database_url or "").strip()
    if not value:
        return "(brak DATABASE_URL)"
    try:
        return make_url(normalize_database_url(value)).render_as_string(hide_password=True)
    except sa.exc.ArgumentError:
        return "(nieprawidłowy DATABASE_URL)"


def check_database_schema(
    database_url: str | None = None,
    *,
    engine: Engine | None = None,
) -> dict[str, list[str]]:
    checked_engine = engine
    if checked_engine is None:
        if not database_url:
            return {}
        checked_engine = create_engine(database_url)
    inspector = sa.inspect(checked_engine)
    tables = set(inspector.get_table_names())
    missing: dict[str, list[str]] = {}
    for table_name, required_columns in REQUIRED_SCHEMA_COLUMNS.items():
        existing_columns = (
            {column["name"] for column in inspector.get_columns(table_name)}
            if table_name in tables
            else set()
        )
        absent = sorted(required_columns - existing_columns)
        if absent:
            missing[table_name] = absent
    return missing


def run_database_upgrade(
    database_url: str,
    *,
    project_root: Path | None = None,
    upgrade: Callable[[AlembicConfig, str], None] = command.upgrade,
) -> None:
    if not str(database_url or "").strip():
        raise RuntimeError("DATABASE_URL jest wymagany do uruchomienia migracji.")
    root = project_root or Path(__file__).resolve().parents[1]
    config = AlembicConfig(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", normalize_database_url(database_url))
    try:
        upgrade(config, "head")
    except Exception as exc:
        raise RuntimeError(
            "Migracja bazy danych nie powiodła się. "
            "Sprawdź logi i uruchom ręcznie: alembic upgrade head"
        ) from exc


def prepare_database_schema(app) -> dict[str, list[str]]:
    database_url = str(app.config.get("DATABASE_URL") or "").strip()
    auto_migrate = bool(app.config.get("AUTO_DB_MIGRATE"))
    app.extensions["database_schema_missing"] = {}
    app.extensions["database_migration_status"] = {}
    if not database_url:
        return {}

    safe_url = redact_database_url(database_url)
    if auto_migrate:
        app.logger.info(
            "AUTO_DB_MIGRATE=true; uruchamianie alembic upgrade head database_url=%s",
            safe_url,
        )
        run_database_upgrade(database_url)
    elif app.config.get("AUTO_CREATE_DB_SCHEMA"):
        app.logger.info(
            "Pominięto walidację migracji: AUTO_CREATE_DB_SCHEMA jest włączone."
        )
        return {}

    migration_status = {}
    try:
        migration_status = database_migration_status(database_url)
        app.extensions["database_migration_status"] = migration_status
        if migration_status["state"] != "head":
            app.logger.error(
                "%s state=%s current=%s heads=%s database_url=%s",
                MIGRATION_HINT,
                migration_status["state"],
                ",".join(migration_status["current"]) or "-",
                ",".join(migration_status["heads"]) or "-",
                safe_url,
            )
            if auto_migrate:
                raise RuntimeError("Automatyczna migracja nie doprowadziła bazy do Alembic head.")
    except RuntimeError:
        raise
    except Exception as exc:
        app.logger.exception("Nie udało się odczytać stanu Alembic. database_url=%s", safe_url)
        if auto_migrate:
            raise RuntimeError("Nie udało się potwierdzić Alembic head po automatycznej migracji.") from exc

    try:
        missing = check_database_schema(database_url)
    except Exception as exc:
        app.logger.exception(
            "Nie udało się zweryfikować schematu bazy. database_url=%s "
            "auto_db_migrate=%s",
            safe_url,
            auto_migrate,
        )
        if auto_migrate:
            raise RuntimeError(
                "Nie udało się zweryfikować schematu po automatycznej migracji."
            ) from exc
        return {}

    app.extensions["database_schema_missing"] = missing
    if missing:
        for table_name, columns in missing.items():
            app.logger.error(
                "%s table=%s missing_columns=%s database_url=%s "
                "auto_db_migrate=%s",
                MIGRATION_HINT,
                table_name,
                ",".join(columns),
                safe_url,
                auto_migrate,
            )
        if auto_migrate:
            raise RuntimeError(
                f"{MIGRATION_HINT}. Automatyczna migracja nie uzupełniła schematu."
            )
    else:
        app.logger.info(
            "Schemat rozszerzony bazy danych jest aktualny. database_url=%s "
            "auto_db_migrate=%s",
            safe_url,
            auto_migrate,
        )
    return missing
