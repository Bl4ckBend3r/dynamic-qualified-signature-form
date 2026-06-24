from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.p4_schema_check_service import P4SchemaCheckService


def write_report(report: dict, report_path: str | None) -> None:
    if not report_path:
        return
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check P4 schema compatibility without modifying the database.")
    parser.add_argument("--database-url", default=None, help="Database URL. Defaults to DATABASE_URL env var.")
    parser.add_argument("--report", default=None, help="Optional JSON report path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = args.database_url or os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL is required for P4 schema checks.", file=sys.stderr)
        return 2
    try:
        engine = create_engine(database_url, future=True)
        report = P4SchemaCheckService().check_schema(engine)
        write_report(report, args.report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["schema_ready"] else 1
    except Exception as exc:
        report = {
            "schema_ready": False,
            "missing_tables": [],
            "missing_columns": {},
            "errors": [
                {
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                }
            ],
        }
        try:
            write_report(report, args.report)
        except Exception:
            pass
        print(f"Technical P4 schema check error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
