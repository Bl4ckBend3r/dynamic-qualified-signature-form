from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import create_session_factory
from services.legacy_fallback_report_service import LegacyFallbackReportService


def write_report(report: dict, report_path: str | None) -> None:
    if not report_path:
        return
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Report legacy fallback usage after P4.2.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of processed submissions.")
    parser.add_argument("--submission-id", default=None, help="Process one public submission_id.")
    parser.add_argument("--report", default=None, help="Optional JSON report path.")
    parser.add_argument("--database-url", default=None, help="Database URL. Defaults to DATABASE_URL env var.")
    parser.add_argument("--output-dir", default=os.getenv("NEXTCLOUD_OUTPUT_DIR", "output"), help="Output directory used for expected storage paths.")
    parser.add_argument("--strict-document-metadata-read", action="store_true", help="Report strict document metadata failures.")
    parser.add_argument("--strict-workflow-history-read", action="store_true", help="Report strict workflow history failures.")
    parser.add_argument("--strict-decision-audit-read", action="store_true", help="Report strict decision audit failures.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = args.database_url or os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL is required for legacy fallback reporting.", file=sys.stderr)
        return 2

    session_factory = create_session_factory(database_url)
    service = LegacyFallbackReportService(
        output_dir=args.output_dir,
        strict_document_metadata_read=args.strict_document_metadata_read,
        strict_workflow_history_read=args.strict_workflow_history_read,
        strict_decision_audit_read=args.strict_decision_audit_read,
    )
    with session_factory() as db:
        report = service.build_fallback_report(db, limit=args.limit, submission_id=args.submission_id)
    write_report(report, args.report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
