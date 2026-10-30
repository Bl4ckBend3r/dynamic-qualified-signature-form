from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from database import create_session_factory
from models import Form, FormSubmission, FormVersion, SubmissionFile, SubmissionTraining, SubmissionWorkflowEvent


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def seed(database_url: str, storage_root: Path, expectations_path: Path) -> None:
    documents = {
        "generated/declaration.pdf": b"synthetic generated declaration\n",
        "signed/agreement.pdf": b"synthetic signed agreement\n",
        "attachments/participant.txt": b"synthetic participant attachment\n",
    }
    for relative, content in documents.items():
        path = storage_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    session_factory = create_session_factory(database_url)
    with session_factory() as db:
        form = Form(slug="p3-restore-fixture", name="P3 synthetic restore fixture", definition_json={})
        db.add(form)
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
        submission = FormSubmission(
            submission_id="P3-RESTORE-0001",
            form_slug=form.slug,
            form_name=form.name,
            form_version_id=version.id,
            data_json={"fixture": True},
            selected_trainings="[]",
            training_agreements="[]",
        )
        db.add(submission)
        db.flush()
        db.add(
            SubmissionTraining(
                submission_id=submission.id,
                training_id="training-p3",
                training_name_snapshot="Synthetic training",
                training_price_snapshot="100.00",
                training_snapshot={"id": "training-p3", "capacity": 20},
                status="agreement_uploaded_by_beneficiary",
                is_locked=True,
            )
        )
        document_types = ("declaration", "signed_training_agreement", "participant_attachment")
        for (relative, content), document_type in zip(documents.items(), document_types, strict=True):
            db.add(
                SubmissionFile(
                    submission_id=submission.id,
                    public_submission_id=submission.submission_id,
                    form_slug=form.slug,
                    document_id=document_type,
                    document_type=document_type,
                    file_role="fixture",
                    storage_provider="controlled_archive",
                    filename=Path(relative).name,
                    storage_path=relative,
                    size_bytes=len(content),
                    checksum_sha256=_sha256(content),
                    status="signed" if document_type == "signed_training_agreement" else "generated",
                )
            )
        db.add(
            SubmissionWorkflowEvent(
                submission_id=submission.id,
                public_submission_id=submission.submission_id,
                form_slug=form.slug,
                new_status="RESTORE_FIXTURE_READY",
                new_step="restore_fixture",
                source="p3_restore_fixture",
            )
        )
        db.commit()

    expectations = {
        "exact_counts": {
            "forms": 1,
            "form_versions": 1,
            "form_submissions": 1,
            "submission_trainings": 1,
            "submission_files": 3,
            "submission_workflow_events": 1,
        },
        "documents": [
            {"path": relative, "sha256": _sha256(content)} for relative, content in documents.items()
        ],
    }
    expectations_path.parent.mkdir(parents=True, exist_ok=True)
    expectations_path.write_text(json.dumps(expectations, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--storage-root", type=Path, required=True)
    parser.add_argument("--expectations", type=Path, required=True)
    args = parser.parse_args()
    seed(args.database_url, args.storage_root, args.expectations)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
