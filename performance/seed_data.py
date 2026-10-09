from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from database import create_session_factory
from models import Form, FormSubmission, FormVersion, User
from werkzeug.security import generate_password_hash


def seed(database_url: str, count: int, output: Path) -> None:
    session_factory = create_session_factory(database_url)
    admin_email = "performance-admin@example.invalid"
    admin_password = "Synthetic-Performance-Only-Password-42!"
    with session_factory() as db:
        admin = User(
            email=admin_email,
            password_hash=generate_password_hash(admin_password),
            role="super_admin",
            is_active=True,
            is_blocked=False,
        )
        form = Form(slug="performance-synthetic", name="Synthetic performance form", definition_json={})
        db.add_all([admin, form])
        db.flush()
        version = FormVersion(
            form_id=form.id,
            version_major=1,
            version_minor=0,
            version_label="1.0",
            status="published",
            definition_json={"fields": [], "workflow": {}},
        )
        db.add(version)
        db.flush()
        public = []
        now = datetime.now(timezone.utc)
        for index in range(count):
            token = f"synthetic-access-token-{index:06d}"
            submission_id = f"PERF-{index:08d}"
            row = FormSubmission(
                submission_id=submission_id,
                form_slug=form.slug,
                form_name=form.name,
                form_version_id=version.id,
                access_token=token,
                data_json={"synthetic_index": index},
                email=f"participant-{index}@example.invalid",
                imiona=f"Synthetic{index}",
                nazwisko="Participant",
                process_status="FORM_SUBMITTED" if index % 3 else "WAITING_FOR_OFFICER_DECISION",
                workflow_step="FORM_SUBMITTED",
                selected_trainings="[]",
                training_agreements="[]",
                due_at=now - timedelta(days=1) if index % 10 == 0 else now + timedelta(days=7),
            )
            db.add(row)
            if index < 2:
                public.append({"submission_id": submission_id, "token": token})
        db.commit()
        form_id = form.id
        form_slug = form.slug
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "admin_email": admin_email,
                "admin_password": admin_password,
                "form_id": form_id,
                "form_slug": form_slug,
                "submission_count": count,
                "public_submissions": public,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed an isolated database with synthetic P3 performance data.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--count", "--submissions", dest="count", type=int, choices=(100, 1000, 10000), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    seed(args.database_url, args.count, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
