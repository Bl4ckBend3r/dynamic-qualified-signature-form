from __future__ import annotations

import argparse
from datetime import datetime


def process_deadlines(*, now: datetime | None = None) -> dict[str, int]:
    from app import create_app
    from database import create_session_factory

    app = create_app()
    database_url = str(app.config.get("DATABASE_URL") or "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL jest wymagany do przetwarzania deadline SLA.")
    with app.app_context(), create_session_factory(database_url)() as db:
        return app.extensions["services"].workflow_sla_service.process_due(db, now=now)


def main() -> int:
    parser = argparse.ArgumentParser(description="Process idempotent workflow SLA reminders and escalations.")
    parser.add_argument("--now", help="Controlled ISO timestamp for diagnostics/tests; omit in production.")
    args = parser.parse_args()
    now = datetime.fromisoformat(args.now) if args.now else None
    result = process_deadlines(now=now)
    print(
        "SLA deadlines: "
        + ", ".join(f"{key}={value}" for key, value in result.items())
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
