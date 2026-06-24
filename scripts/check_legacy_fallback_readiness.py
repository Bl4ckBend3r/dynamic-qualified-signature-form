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
from services.legacy_fallback_readiness_service import LegacyFallbackReadinessService
from services.legacy_fallback_report_service import LegacyFallbackReportService


def write_report(report: dict, report_path: str | None) -> None:
    if not report_path:
        return
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check readiness for disabling selected legacy read fallbacks.")
    parser.add_argument(
        "--area",
        choices=["all", "documents", "workflow", "decisions"],
        default="all",
        help="Area to validate before enabling strict read mode.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit number of processed submissions.")
    parser.add_argument("--submission-id", default=None, help="Check one public submission_id.")
    parser.add_argument("--report", default=None, help="Optional JSON readiness report path.")
    parser.add_argument("--recommend", action="store_true", help="Generate rollout recommendations for all areas.")
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
        print("DATABASE_URL is required for legacy fallback readiness checks.", file=sys.stderr)
        return 2

    try:
        session_factory = create_session_factory(database_url)
        service = LegacyFallbackReadinessService(
            report_service=LegacyFallbackReportService(output_dir=args.output_dir)
        )
        with session_factory() as db:
            if args.recommend:
                report = service.build_rollout_plan(
                    db,
                    limit=args.limit,
                    submission_id=args.submission_id,
                )
            else:
                report = service.build_readiness_report(
                    db,
                    area=args.area,
                    limit=args.limit,
                    submission_id=args.submission_id,
                )
        write_report(report, args.report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ready"] else 1
    except Exception as exc:
        print(f"Technical readiness check error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
