"""Read-only report of attachment files without exact SubmissionFile metadata."""

from __future__ import annotations

import argparse
import json
import re

from sqlalchemy import select

from config import Config
from database import create_session_factory
from models import FormSubmission, SubmissionFile
from services.nextcloud_storage import create_nextcloud_storage_from_env


_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")
_GENERATED_FILENAME = re.compile(r"^[0-9a-f]{32}\.[a-z0-9]+$")


def report_orphan_attachments(session_factory, storage, *, submission_id=None, limit=None) -> dict:
    """Compare canonical attachment paths; never read bytes or mutate either store."""
    report = {"dry_run": True, "submissions": 0, "files_examined": 0, "candidates": []}
    with session_factory() as db:
        query = db.query(FormSubmission).order_by(FormSubmission.id)
        if submission_id:
            query = query.filter(FormSubmission.submission_id == submission_id)
        if limit:
            query = query.limit(limit)
        for submission in query:
            report["submissions"] += 1
            if not all(_SEGMENT.fullmatch(str(value or "")) for value in (
                submission.form_slug, submission.submission_id,
            )):
                continue
            root = "/".join((
                str(storage.output_dir).strip("/"), submission.form_slug,
                "submissions", submission.submission_id, "attachments",
            ))
            recorded = set(db.execute(select(SubmissionFile.storage_path).where(
                SubmissionFile.submission_id == submission.id,
            )).scalars())
            for field_key, is_dir in storage.list_children(root):
                if not is_dir or not _SEGMENT.fullmatch(field_key):
                    continue
                field_root = f"{root}/{field_key}"
                for filename, file_is_dir in storage.list_children(field_root):
                    if file_is_dir or not _GENERATED_FILENAME.fullmatch(filename):
                        continue
                    path = f"{field_root}/{filename}"
                    report["files_examined"] += 1
                    if path not in recorded:
                        report["candidates"].append({
                            "submission_id": submission.submission_id,
                            "form_slug": submission.form_slug,
                            "field_key": field_key,
                            "storage_path": path,
                            "operation_id": None,
                            "classification": "untracked_path_requires_manual_review",
                        })
        db.rollback()
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=Config.DATABASE_URL)
    parser.add_argument("--submission-id")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("DATABASE_URL is required")
    report = report_orphan_attachments(
        create_session_factory(args.database_url),
        create_nextcloud_storage_from_env(),
        submission_id=args.submission_id,
        limit=args.limit,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
