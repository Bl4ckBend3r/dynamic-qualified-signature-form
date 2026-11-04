from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from config import Config
from database import create_session_factory
from models import Form, FormSubmission
from services.workflow_config_service import WorkflowConfigNormalizer, WorkflowConfigValidator
from services.workflow_state_service import layered_state_from_legacy


def backfill(*, apply: bool = False, report_path: str = "") -> dict:
    session_factory = create_session_factory(Config.DATABASE_URL)
    report = {
        "mode": "apply" if apply else "dry-run",
        "submissions_scanned": 0,
        "submissions_changed": 0,
        "forms_scanned": 0,
        "forms_changed": 0,
        "unknown_legacy_statuses": Counter(),
        "status_mapping": Counter(),
        "form_validation_errors": {},
    }
    with session_factory() as session:
        for submission in session.execute(select(FormSubmission)).scalars():
            report["submissions_scanned"] += 1
            state = layered_state_from_legacy(
                submission.process_status,
                workflow_step=submission.workflow_stage or submission.workflow_step,
                officer_decision=submission.officer_decision,
                document_states=submission.document_states,
                final_outcome=submission.final_outcome,
            )
            report["status_mapping"][
                f"{state.legacy_process_status or '<empty>'} -> {state.workflow_stage}"
            ] += 1
            if state.used_fallback:
                report["unknown_legacy_statuses"][state.legacy_process_status or "<empty>"] += 1
            values = {
                "workflow_stage": state.workflow_stage,
                "final_outcome": state.final_outcome.value,
                "document_states": state.document_states,
                "legacy_process_status": state.legacy_process_status,
            }
            if any(getattr(submission, key) != value for key, value in values.items()):
                report["submissions_changed"] += 1
                if apply:
                    for key, value in values.items():
                        setattr(submission, key, value)

        normalizer = WorkflowConfigNormalizer()
        validator = WorkflowConfigValidator()
        for form in session.execute(select(Form)).scalars():
            report["forms_scanned"] += 1
            definition = dict(form.definition_json or {})
            old_workflow = definition.get("workflow") or {}
            workflow = normalizer.normalize(old_workflow)
            errors = validator.validate(workflow, definition)
            if errors:
                report["form_validation_errors"][form.slug] = errors
            if workflow != old_workflow:
                report["forms_changed"] += 1
                if apply:
                    definition["workflow"] = workflow
                    form.definition_json = definition
        if apply:
            session.commit()
        else:
            session.rollback()

    report["unknown_legacy_statuses"] = dict(report["unknown_legacy_statuses"])
    report["status_mapping"] = dict(report["status_mapping"])
    if report_path:
        Path(report_path).write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill layered workflow state and workflow schema v2.")
    parser.add_argument("--apply", action="store_true", help="Persist changes. The default is a dry run.")
    parser.add_argument("--report", default="", help="Optional JSON report path.")
    args = parser.parse_args()
    print(json.dumps(backfill(apply=args.apply, report_path=args.report), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
