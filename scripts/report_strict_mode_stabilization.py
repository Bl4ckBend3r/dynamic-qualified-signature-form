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
from services.strict_mode_stabilization_service import StrictModeStabilizationService


def env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "tak", "on"}


def write_report(report: dict, report_path: str | None) -> None:
    if not report_path:
        return
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Report strict mode stabilization status without modifying data.")
    parser.add_argument(
        "--area",
        choices=["all", "documents", "workflow", "decisions"],
        default="all",
        help="Area to include in the stabilization report.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit number of processed submissions.")
    parser.add_argument("--submission-id", default=None, help="Report one public submission_id.")
    parser.add_argument("--report", default=None, help="Optional JSON report path.")
    parser.add_argument("--database-url", default=None, help="Database URL. Defaults to DATABASE_URL env var.")
    parser.add_argument(
        "--output-dir",
        default=os.getenv("NEXTCLOUD_OUTPUT_DIR", "output"),
        help="Output directory used for expected document storage paths.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = args.database_url or os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL is required for strict mode stabilization reporting.", file=sys.stderr)
        return 2

    try:
        session_factory = create_session_factory(database_url)
        service = StrictModeStabilizationService(
            report_service=LegacyFallbackReportService(output_dir=args.output_dir),
            strict_flags={
                "STRICT_DOCUMENT_METADATA_READ": env_bool("STRICT_DOCUMENT_METADATA_READ"),
                "STRICT_WORKFLOW_HISTORY_READ": env_bool("STRICT_WORKFLOW_HISTORY_READ"),
                "STRICT_DECISION_AUDIT_READ": env_bool("STRICT_DECISION_AUDIT_READ"),
            },
        )
        with session_factory() as db:
            report = service.build_stabilization_report(
                db,
                area=args.area,
                limit=args.limit,
                submission_id=args.submission_id,
            )
        write_report(report, args.report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"Technical strict mode stabilization report error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
