from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from database import get_database_url
from services.form_config_service import FormConfigService
from services.form_draft_service import FormDraftService
from services.database_schema_service import (
    MIGRATION_HINT,
    check_database_schema,
    database_migration_status,
    redact_database_url,
    run_database_upgrade,
)
from validators.form_config_validator import FormConfigValidator


def validate_form(filename: str, skip_template_check: bool = False, template_root: str | Path | None = None) -> int:
    path = Path(filename)
    with path.open("r", encoding="utf-8") as handle:
        raw_config = json.load(handle)
    form_config = FormConfigService().normalize_form_config(raw_config)
    errors = FormConfigValidator(
        template_root=template_root or Path.cwd() / "templates",
        skip_template_check=skip_template_check,
    ).validate(form_config)
    if errors:
        print("Form config validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Form config validation passed.")
    return 0


def db_check(database_url: str | None = None) -> int:
    selected_url = str(database_url or get_database_url() or "").strip()
    if not selected_url:
        print("DATABASE_URL jest wymagany.")
        return 2
    try:
        migration_status = database_migration_status(selected_url)
        missing = check_database_schema(selected_url)
    except Exception as exc:
        print(
            f"Nie udało się sprawdzić schematu: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2
    print(f"Database: {redact_database_url(selected_url)}")
    print(f"Alembic current: {', '.join(migration_status['current']) or '(brak)'}")
    print(f"Alembic head: {', '.join(migration_status['heads']) or '(brak)'}")
    print(f"Alembic state: {migration_status['state']}")
    if migration_status["state"] != "head":
        print(MIGRATION_HINT)
    if not missing:
        if migration_status["state"] == "head":
            print("Schemat rozszerzony jest aktualny.")
            return 0
        return 1
    print(MIGRATION_HINT)
    print("Tabela | Brakujące kolumny")
    print("--- | ---")
    for table_name, columns in missing.items():
        print(f"{table_name} | {', '.join(columns)}")
    return 1


def db_upgrade(database_url: str | None = None) -> int:
    selected_url = str(database_url or get_database_url() or "").strip()
    if not selected_url:
        print("DATABASE_URL jest wymagany.", file=sys.stderr)
        return 2
    try:
        run_database_upgrade(selected_url)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("Migracja zakończona: alembic upgrade head")
    return 0


def expire_form_drafts(database_url: str | None = None) -> int:
    selected_url = str(database_url or get_database_url() or "").strip()
    if not selected_url:
        print("DATABASE_URL jest wymagany.", file=sys.stderr)
        return 2
    from database import create_session_factory

    with create_session_factory(selected_url)() as db:
        count = FormDraftService.mark_expired(db)
    print(f"Oznaczono wygasłe wersje robocze: {count}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate-form")
    validate_parser.add_argument("filename")
    validate_parser.add_argument("--skip-template-check", action="store_true")
    validate_parser.add_argument("--template-root")
    check_parser = subparsers.add_parser("db-check")
    check_parser.add_argument("--database-url")
    upgrade_parser = subparsers.add_parser("db-upgrade")
    upgrade_parser.add_argument("--database-url")
    expire_parser = subparsers.add_parser("drafts-expire")
    expire_parser.add_argument("--database-url")
    args = parser.parse_args()
    if args.command == "validate-form":
        return validate_form(
            args.filename,
            skip_template_check=args.skip_template_check,
            template_root=args.template_root,
        )
    if args.command == "db-check":
        return db_check(args.database_url)
    if args.command == "db-upgrade":
        return db_upgrade(args.database_url)
    if args.command == "drafts-expire":
        return expire_form_drafts(args.database_url)
    return 1


if __name__ == "__main__":
    sys.exit(main())
