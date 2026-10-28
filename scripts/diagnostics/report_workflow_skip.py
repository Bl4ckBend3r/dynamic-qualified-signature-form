"""Read-only diagnosis of a submission that appears to have skipped a decision."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import Config
from database import create_session_factory
from models import FormSubmission, FormVersion, SubmissionDecision, SubmissionFile, SubmissionWorkflowEvent


def _edges(workflow: dict) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for step in workflow.get("steps") or []:
        if not isinstance(step, dict) or not step.get("id"):
            continue
        targets = {str(step[key]) for key in ("next", "completion_next") if step.get(key)}
        targets.update(str(item["next"]) for item in step.get("transitions") or [] if isinstance(item, dict) and item.get("next"))
        targets.update(str(value) for value in (step.get("decisions") or {}).values() if value)
        result[str(step["id"])] = targets
    for decision in workflow.get("decision_types") or []:
        if isinstance(decision, dict) and decision.get("step_id") and decision.get("target_step"):
            result.setdefault(str(decision["step_id"]), set()).add(str(decision["target_step"]))
    return result


def _reachable(edges: dict[str, set[str]], start: str, target: str, *, excluded: str = "") -> bool:
    pending = [start]
    seen = set()
    while pending:
        current = pending.pop()
        if current == excluded or current in seen:
            continue
        if current == target:
            return True
        seen.add(current)
        pending.extend(edges.get(current, set()) - seen)
    return False


def mandatory_decision_steps(workflow: dict, current: str) -> list[str]:
    """Find decision gates on every configured route from initial to current."""
    initial = str(workflow.get("initial_step") or "")
    edges = _edges(workflow)
    if not initial or not _reachable(edges, initial, current):
        return []
    return [
        str(step["id"])
        for step in workflow.get("steps") or []
        if isinstance(step, dict) and step.get("id") != current
        and (step.get("stage_type") == "decision" or step.get("requires_officer_action"))
        and not _reachable(edges, initial, current, excluded=str(step["id"]))
    ]


def build_report(db, public_submission_id: str | None = None, *, submission_pk: int | None = None, form_id: int | None = None) -> dict:
    if (public_submission_id is None) == (submission_pk is None):
        raise ValueError("Podaj dokładnie jeden identyfikator zgłoszenia.")
    submission = (
        db.scalar(select(FormSubmission).where(FormSubmission.submission_id == public_submission_id))
        if public_submission_id is not None else db.get(FormSubmission, submission_pk)
    )
    if submission is None:
        raise LookupError("Nie znaleziono zgłoszenia.")
    version = db.get(FormVersion, submission.form_version_id) if submission.form_version_id else None
    if form_id is not None and (version is None or version.form_id != form_id):
        raise LookupError("Zgłoszenie nie należy do wskazanego formularza lub nie ma FormVersion.")
    definition = dict(version.definition_json or {}) if version else {}
    workflow = dict(definition.get("workflow") or {})
    events = db.scalars(select(SubmissionWorkflowEvent).where(
        SubmissionWorkflowEvent.submission_id == submission.id,
    ).order_by(SubmissionWorkflowEvent.created_at, SubmissionWorkflowEvent.id)).all()
    decisions = db.scalars(select(SubmissionDecision).where(SubmissionDecision.submission_id == submission.id)).all()
    files = db.scalars(select(SubmissionFile).where(SubmissionFile.submission_id == submission.id)).all()
    current = str(submission.workflow_step or submission.workflow_stage or "")
    gates = mandatory_decision_steps(workflow, current)
    visited = {str(value) for event in events for value in (event.previous_step, event.new_step) if value}
    unvisited = [step for step in gates if step not in visited]
    later_files = [file for file in files if file.document_type not in {"form_pdf", "signed_form_pdf"} and file.status != "superseded"]
    signed_document = bool(
        submission.declaration_signed == "Tak" or submission.declaration_signature_valid == "Tak"
        or submission.declaration_signed_filename
        or (submission.data_json or {}).get("_signed_declaration_snapshot")
    )
    recorded_decision = bool(submission.officer_decision or decisions or any(event.decision_code for event in events))
    return {
        "submission_id": submission.submission_id,
        "form_slug": submission.form_slug,
        "form_version_id": submission.form_version_id,
        "version_available": version is not None,
        "current": {"workflow_step": submission.workflow_step, "workflow_stage": submission.workflow_stage,
                    "process_status": submission.process_status},
        "workflow": {"flow_mode": workflow.get("flow_mode"), "initial_step": workflow.get("initial_step"),
                     "steps": [{key: step.get(key) for key in ("id", "admin_label", "stage_type", "type", "action", "status", "next", "transitions", "requires_officer_action", "completion_next", "document_id")}
                               for step in workflow.get("steps") or [] if isinstance(step, dict)],
                     "decision_types": [{key: item.get(key) for key in ("code", "step_id", "target_step", "scope")}
                                        for item in workflow.get("decision_types") or [] if isinstance(item, dict)]},
        "events": [{"id": event.id, "previous_status": event.previous_status, "new_status": event.new_status,
                    "previous_step": event.previous_step, "new_step": event.new_step,
                    "source": event.source, "reason": event.reason, "actor_role": event.actor_role,
                    "created_at": event.created_at.isoformat() if event.created_at else None,
                    "side_effect_keys": sorted((event.side_effects or {}).keys())}
                   for event in events],
        "assessment": {"mandatory_decision_steps": gates, "unvisited_decision_steps": unvisited,
                       "recorded_decision": recorded_decision, "later_document_types": sorted({file.document_type for file in later_files}),
                       "signed_document": signed_document,
                       "automatic_repair_safe": False,
                       "reason": "Wymaga przeglądu konfiguracji wersji, historii i skutków późniejszego etapu; skrypt niczego nie zmienia."},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--submission-id", help="Publiczny submission_id (UUID).")
    identity.add_argument("--submission-pk", type=int, help="Wewnętrzny numer zgłoszenia z adresu panelu.")
    parser.add_argument("--form-id", type=int, help="Numer formularza z adresu panelu; weryfikuje wybór rekordu.")
    parser.add_argument("--database-url", default=Config.DATABASE_URL, help="Domyślnie DATABASE_URL z konfiguracji aplikacji.")
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("DATABASE_URL jest wymagany.")
    with create_session_factory(args.database_url)() as db:
        report = build_report(db, args.submission_id, submission_pk=args.submission_pk, form_id=args.form_id)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
