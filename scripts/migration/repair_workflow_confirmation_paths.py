from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import Config
from database import create_session_factory
from models import Form
from services.workflow_config_service import repair_form_definition_agreement_confirmation


def repair_existing_form_workflows(session_factory, *, apply: bool = False) -> dict:
    report = {"dry_run": not apply, "processed": 0, "repaired": 0, "forms": []}
    with session_factory() as db:
        for form in db.query(Form).order_by(Form.id).all():
            report["processed"] += 1
            repaired, changed = repair_form_definition_agreement_confirmation(form.definition_json)
            if not changed:
                continue
            report["repaired"] += 1
            report["forms"].append({"id": form.id, "slug": form.slug})
            if apply:
                form.definition_json = repaired
        if apply:
            db.commit()
        else:
            db.rollback()
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Repair the known training agreement shortcut that bypasses office confirmation."
    )
    parser.add_argument("--apply", action="store_true", help="Persist repairs; default is dry-run.")
    parser.add_argument("--database-url", default=None, help="Defaults to DATABASE_URL.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = args.database_url or Config.DATABASE_URL
    if not database_url:
        print("DATABASE_URL is required.", file=sys.stderr)
        return 2
    report = repair_existing_form_workflows(
        create_session_factory(database_url),
        apply=bool(args.apply),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
