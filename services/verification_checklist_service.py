from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from models import (
    Form,
    FormPermission,
    FormSubmission,
    FormVersion,
    SubmissionChecklistEvidence,
    SubmissionChecklistItemResult,
    SubmissionChecklistResultHistory,
    SubmissionFile,
    User,
    VerificationChecklistDefinition,
    VerificationChecklistItemDefinition,
)


RESULTS = {"yes", "no", "not_applicable", "pending"}


class VerificationChecklistError(ValueError):
    pass


class VerificationChecklistPermissionError(VerificationChecklistError):
    pass


@dataclass(frozen=True)
class ChecklistValidation:
    valid: bool
    errors: tuple[str, ...]


class VerificationChecklistService:
    """Owns versioned checklist definitions, officer results and decision gates."""

    def _permission(self, db, user: User, form: Form) -> FormPermission | None:
        if user.role == "super_admin":
            return None
        return db.execute(select(FormPermission).where(
            FormPermission.user_id == user.id, FormPermission.form_id == form.id
        )).scalar_one_or_none()

    def can_review(self, db, user: User, form: Form) -> bool:
        if user.role == "super_admin":
            return True
        permission = self._permission(db, user, form)
        return bool(user.is_active and not user.is_blocked and permission and permission.can_review)

    def can_make_decision(self, db, user: User, form: Form) -> bool:
        if user.role == "super_admin":
            return True
        permission = self._permission(db, user, form)
        return bool(user.role == "admin" and permission and permission.can_make_decision)

    def can_view_sensitive_data(self, db, user: User, form: Form) -> bool:
        if user.role == "super_admin":
            return True
        permission = self._permission(db, user, form)
        return bool(permission and permission.can_view_sensitive_data)

    def list_for_version(self, db, form_version_id: int, *, active_only: bool = False):
        query = select(VerificationChecklistDefinition).options(
            selectinload(VerificationChecklistDefinition.items)
        ).where(VerificationChecklistDefinition.form_version_id == form_version_id)
        if active_only:
            query = query.where(VerificationChecklistDefinition.active.is_(True))
        return db.execute(query.order_by(
            VerificationChecklistDefinition.position, VerificationChecklistDefinition.id
        )).scalars().all()

    def create_checklist(self, db, version: FormVersion, *, name: str, workflow_step: str, position: int = 0):
        self._require_draft(version)
        name = str(name or "").strip()
        workflow_step = str(workflow_step or "").strip()
        if not name or not workflow_step:
            raise VerificationChecklistError("Nazwa i etap workflow są wymagane.")
        self._require_workflow_step(version, workflow_step)
        checklist = VerificationChecklistDefinition(
            form_version_id=version.id, name=name, workflow_step=workflow_step, position=position, active=True
        )
        db.add(checklist)
        db.flush()
        return checklist

    def add_item(self, db, checklist: VerificationChecklistDefinition, **values):
        self._require_draft(checklist.form_version)
        key = re.sub(r"[^a-z0-9_]+", "_", str(values.get("key") or "").strip().lower()).strip("_")
        label = str(values.get("label") or "").strip()
        if not key or not label:
            raise VerificationChecklistError("Klucz i treść kryterium są wymagane.")
        item = VerificationChecklistItemDefinition(
            checklist_id=checklist.id,
            key=key,
            label=label,
            description=str(values.get("description") or "").strip(),
            position=int(values.get("position") or 0),
            blocking=bool(values.get("blocking")),
            required=bool(values.get("required", True)),
            document_required=bool(values.get("document_required")),
            allow_not_applicable=bool(values.get("allow_not_applicable", True)),
            sensitive=bool(values.get("sensitive")),
            rules_json=dict(values.get("rules_json") or {}),
        )
        db.add(item)
        db.flush()
        return item

    def delete_item(self, db, item: VerificationChecklistItemDefinition) -> None:
        self._require_draft(item.checklist.form_version)
        db.delete(item)

    def delete_checklist(self, db, checklist: VerificationChecklistDefinition) -> None:
        self._require_draft(checklist.form_version)
        db.delete(checklist)

    def clone_definitions(self, db, source_version_id: int, target_version: FormVersion) -> None:
        self._require_draft(target_version)
        for checklist in self.list_for_version(db, source_version_id):
            clone = VerificationChecklistDefinition(
                form_version_id=target_version.id, name=checklist.name, workflow_step=checklist.workflow_step,
                position=checklist.position, active=checklist.active,
            )
            db.add(clone)
            db.flush()
            for item in checklist.items:
                db.add(VerificationChecklistItemDefinition(
                    checklist_id=clone.id, key=item.key, label=item.label, description=item.description,
                    position=item.position, blocking=item.blocking, required=item.required,
                    document_required=item.document_required, allow_not_applicable=item.allow_not_applicable,
                    sensitive=item.sensitive, rules_json=dict(item.rules_json or {}),
                ))

    def validate_version(self, db, version: FormVersion) -> list[str]:
        steps = (((version.definition_json or {}).get("workflow") or {}).get("steps") or [])
        step_ids = {str(step.get("id") or "") for step in steps if isinstance(step, dict)}
        errors = []
        for checklist in self.list_for_version(db, version.id):
            if checklist.workflow_step not in step_ids:
                errors.append(f"Checklista {checklist.name} wskazuje nieistniejący etap {checklist.workflow_step}.")
            if checklist.active and not checklist.items:
                errors.append(f"Aktywna checklista {checklist.name} nie zawiera kryteriów.")
        return errors

    def build_submission_view(self, db, submission: FormSubmission, *, include_sensitive: bool = True):
        if not submission.form_version_id:
            return []
        checklists = self.list_for_version(db, submission.form_version_id, active_only=True)
        results = db.execute(select(SubmissionChecklistItemResult).options(
            selectinload(SubmissionChecklistItemResult.officer),
            selectinload(SubmissionChecklistItemResult.evidence_links).selectinload(SubmissionChecklistEvidence.submission_file),
            selectinload(SubmissionChecklistItemResult.history),
        ).where(SubmissionChecklistItemResult.submission_id == submission.id)).scalars().all()
        result_by_item = {result.checklist_item_definition_id: result for result in results}
        view = []
        for checklist in checklists:
            items = []
            for item in checklist.items:
                if item.sensitive and not include_sensitive:
                    items.append({"item": item, "restricted": True, "result": None})
                else:
                    items.append({"item": item, "restricted": False, "result": result_by_item.get(item.id)})
            view.append({"checklist": checklist, "items": items})
        return view

    def save_result(self, db, submission: FormSubmission, item: VerificationChecklistItemDefinition, *,
                    result: str, comment: str, evidence_file_ids: list[int], officer: User):
        form = db.execute(select(Form).where(Form.slug == submission.form_slug)).scalar_one()
        if not self.can_review(db, officer, form):
            raise VerificationChecklistPermissionError("Brak uprawnienia do weryfikacji checklisty.")
        if item.checklist.form_version_id != submission.form_version_id:
            raise VerificationChecklistError("Kryterium nie należy do wersji formularza tego zgłoszenia.")
        result = str(result or "pending").strip()
        if result not in RESULTS:
            raise VerificationChecklistError("Nieprawidłowy wynik weryfikacji.")
        if result == "not_applicable" and not item.allow_not_applicable:
            raise VerificationChecklistError("Dla tego kryterium nie można wybrać „Nie dotyczy”.")
        evidence_ids = list(dict.fromkeys(int(value) for value in evidence_file_ids))
        evidence = []
        if evidence_ids:
            evidence = db.execute(select(SubmissionFile).where(SubmissionFile.id.in_(evidence_ids))).scalars().all()
            if len(evidence) != len(evidence_ids) or any(file.submission_id != submission.id for file in evidence):
                raise VerificationChecklistError("Dokument będący podstawą musi należeć do tego zgłoszenia.")
        record = db.execute(select(SubmissionChecklistItemResult).where(
            SubmissionChecklistItemResult.submission_id == submission.id,
            SubmissionChecklistItemResult.checklist_item_definition_id == item.id,
        )).scalar_one_or_none()
        now = datetime.now(timezone.utc)
        comment = str(comment or "").strip()
        if record is None:
            record = SubmissionChecklistItemResult(
                submission_id=submission.id, checklist_item_definition_id=item.id,
                result=result, comment=comment, officer_user_id=officer.id,
                reviewed_at=now if result != "pending" else None,
            )
            db.add(record)
            db.flush()
        else:
            if record.result != result or record.comment != comment:
                db.add(SubmissionChecklistResultHistory(
                    result_id=record.id, previous_result=record.result, new_result=result,
                    previous_comment=record.comment, new_comment=comment, officer_user_id=officer.id,
                ))
            record.result = result
            record.comment = comment
            record.officer_user_id = officer.id
            record.reviewed_at = now if result != "pending" else None
        db.execute(delete(SubmissionChecklistEvidence).where(SubmissionChecklistEvidence.result_id == record.id))
        db.flush()
        for position, file in enumerate(evidence):
            db.add(SubmissionChecklistEvidence(result_id=record.id, submission_file_id=file.id, position=position))
        db.flush()
        return record

    def validate_for_decision(self, db, submission: FormSubmission, *, decision: str = "accepted", workflow_step: str | None = None,
                              include_sensitive_labels: bool = True) -> ChecklistValidation:
        if decision != "accepted":
            return ChecklistValidation(True, ())
        if not submission.form_version_id:
            return ChecklistValidation(True, ())
        step = str(workflow_step if workflow_step is not None else submission.workflow_step or "").strip()
        checklists = self.list_for_version(db, submission.form_version_id, active_only=True)
        if step:
            checklists = [item for item in checklists if item.workflow_step == step]
        results = db.execute(select(SubmissionChecklistItemResult).options(
            selectinload(SubmissionChecklistItemResult.evidence_links)
        ).where(SubmissionChecklistItemResult.submission_id == submission.id)).scalars().all()
        result_by_item = {result.checklist_item_definition_id: result for result in results}
        errors = []
        for checklist in checklists:
            for item in checklist.items:
                label = item.label if include_sensitive_labels or not item.sensitive else "wrażliwe"
                record = result_by_item.get(item.id)
                value = record.result if record else "pending"
                if item.blocking and value == "no":
                    errors.append(f"Kryterium {label} ma wynik NIE i blokuje akceptację.")
                elif item.required and value == "pending":
                    errors.append(f"Nie oceniono kryterium {label}.")
                elif value == "not_applicable" and not item.allow_not_applicable:
                    errors.append(f"Kryterium {label} nie dopuszcza wyniku NIE DOTYCZY.")
                elif item.document_required and value != "pending" and (not record or not record.evidence_links):
                    errors.append(f"Kryterium {label} wymaga wskazania dokumentu będącego podstawą.")
        return ChecklistValidation(not errors, tuple(errors))

    def status_for_submission(self, db, submission: FormSubmission) -> str:
        validation = self.validate_for_decision(db, submission)
        if any("wynik NIE" in error for error in validation.errors):
            return "blocking_failure"
        if validation.valid:
            return "ready"
        return "incomplete"

    @staticmethod
    def _require_draft(version: FormVersion) -> None:
        if version.status != "draft":
            raise VerificationChecklistError("Checklisty można zmieniać wyłącznie w wersji roboczej formularza.")

    @staticmethod
    def _require_workflow_step(version: FormVersion, workflow_step: str) -> None:
        steps = ((version.definition_json or {}).get("workflow") or {}).get("steps") or []
        step_ids = {str(step.get("id") or "") for step in steps if isinstance(step, dict)}
        if workflow_step not in step_ids:
            raise VerificationChecklistError("Wybrany etap nie istnieje w workflow tej wersji formularza.")
