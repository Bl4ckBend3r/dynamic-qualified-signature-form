from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from typing import Iterable, Mapping

from sqlalchemy import func, select

from models import Form, FormField, FormSubmission, FormVersion, SubmissionFile, SubmissionTraining, SubmissionWorkflowEvent
from services.admin_form_service import sync_form_fields, validate_admin_form_config
from services.compliance_service import ComplianceError, ComplianceService


FORM_VERSION_DRAFT = "draft"
FORM_VERSION_PUBLISHED = "published"
FORM_VERSION_ARCHIVED = "archived"
FORM_VERSION_STATUSES = {
    FORM_VERSION_DRAFT,
    FORM_VERSION_PUBLISHED,
    FORM_VERSION_ARCHIVED,
}


class FormVersionError(ValueError):
    pass


class FormVersionValidationError(FormVersionError):
    def __init__(self, errors: Iterable[str]) -> None:
        self.errors = [str(error) for error in errors if str(error).strip()]
        super().__init__("Nie można opublikować wersji.")


class FormVersionService:
    """Owns immutable form snapshots and the draft-to-published lifecycle."""

    def list_versions(self, db, form_id: int) -> list[FormVersion]:
        return list(
            db.execute(
                select(FormVersion)
                .where(FormVersion.form_id == form_id)
                .order_by(FormVersion.version_major.desc(), FormVersion.version_minor.desc())
            ).scalars()
        )

    def has_versions(self, db, form_id: int) -> bool:
        return bool(
            db.execute(
                select(func.count(FormVersion.id)).where(FormVersion.form_id == form_id)
            ).scalar()
        )

    def editable_draft(
        self,
        db,
        form_id: int,
        *,
        version_id: int | None = None,
    ) -> FormVersion | None:
        query = select(FormVersion).where(
            FormVersion.form_id == form_id,
            FormVersion.status == FORM_VERSION_DRAFT,
        )
        if version_id is not None:
            query = query.where(FormVersion.id == version_id)
        return db.execute(
            query.order_by(FormVersion.version_major.desc(), FormVersion.version_minor.desc())
        ).scalars().first()

    def materialize_editor_mirror(self, db, form: Form, version: FormVersion) -> None:
        """Load one exact draft into the legacy Form/FormField editor adapter."""
        if version.form_id != form.id or version.status != FORM_VERSION_DRAFT:
            raise FormVersionError("Edytor może załadować wyłącznie wskazaną wersję roboczą formularza.")
        self.apply_to_legacy_editor(db, form, deepcopy(version.definition_json or {}))

    def resolve_published(self, db, form_id: int) -> FormVersion | None:
        return db.execute(
            select(FormVersion)
            .where(FormVersion.form_id == form_id, FormVersion.status == FORM_VERSION_PUBLISHED)
            .order_by(FormVersion.published_at.desc(), FormVersion.id.desc())
        ).scalars().first()

    def resolve_for_submission(self, db, submission: FormSubmission) -> FormVersion | None:
        if submission.form_version_id:
            version = db.get(FormVersion, submission.form_version_id)
            if version and version.form.slug == submission.form_slug:
                return version
        return None

    def resolve_rendered_version(self, db, form: Form, version_id: int) -> FormVersion | None:
        return db.execute(
            select(FormVersion).where(
                FormVersion.id == version_id,
                FormVersion.form_id == form.id,
                FormVersion.status.in_((FORM_VERSION_PUBLISHED, FORM_VERSION_ARCHIVED)),
            )
        ).scalar_one_or_none()

    def build_snapshot(self, form: Form, fields: Iterable[FormField] | None = None) -> dict:
        definition = deepcopy(form.definition_json or {})
        source_fields = list(fields) if fields is not None else list(form.fields)
        active_fields = sorted(
            (field for field in source_fields if field.active),
            key=lambda field: (field.sort_order, field.id or 0),
        )
        original_fields = {
            str(field.get("name")): deepcopy(field)
            for field in definition.get("fields") or []
            if isinstance(field, dict) and field.get("name")
        }
        rendered_fields: list[dict] = []
        current_section = None
        for field in active_fields:
            if field.section and field.section != current_section:
                rendered_fields.append({"type": "section", "label": field.section})
                current_section = field.section
            config = original_fields.get(field.name, {})
            config.update(
                {
                    "name": field.name,
                    "label": field.label or field.name,
                    "type": field.type or "text",
                    "required": bool(field.required),
                    "options": deepcopy(field.options or []),
                    "default": field.default_value,
                    "stage": field.stage or "initial_submission",
                    "availability": deepcopy(field.availability_json or config.get("availability") or []),
                }
            )
            rendered_fields.append(config)

        definition["title"] = form.title or form.name
        definition["description"] = form.description
        definition["fields"] = rendered_fields
        definition["user_instruction"] = form.user_instruction or ""
        definition["user_instruction_config"] = deepcopy(form.user_instruction_config or {})
        definition["label_text"] = form.label_text
        definition["label_variant"] = form.label_variant
        definition["label_color"] = form.label_color
        definition["label_background"] = form.label_background
        definition["logo_alignment"] = form.logo_alignment
        definition["project_logo_id"] = form.project_logo_id
        definition["_form_metadata"] = {
            "name": form.name,
            "title": form.title,
            "description": form.description,
            "user_instruction": form.user_instruction or "",
            "user_instruction_config": deepcopy(form.user_instruction_config or {}),
            "label_text": form.label_text,
            "label_variant": form.label_variant,
            "label_color": form.label_color,
            "label_background": form.label_background,
            "logo_id": form.logo_id,
            "project_logo_id": form.project_logo_id,
            "logo_alignment": form.logo_alignment,
        }
        return definition

    def apply_to_legacy_editor(self, db, form: Form, definition: dict) -> None:
        snapshot = deepcopy(definition or {})
        metadata = snapshot.get("_form_metadata") if isinstance(snapshot.get("_form_metadata"), dict) else {}
        def snapshot_value(key: str, fallback):
            if key in metadata:
                return metadata[key]
            if key in snapshot:
                return snapshot[key]
            return fallback

        form.name = str(snapshot_value("name", form.name) or "")
        form.title = str(snapshot_value("title", form.title) or "")
        form.description = str(snapshot_value("description", "") or "")
        form.user_instruction = str(snapshot_value("user_instruction", "") or "") or None
        form.user_instruction_config = deepcopy(snapshot_value("user_instruction_config", {}) or {})
        form.label_text = str(snapshot_value("label_text", "") or "")
        form.label_variant = str(snapshot_value("label_variant", "project") or "project")
        form.label_color = str(snapshot_value("label_color", "#b38d45") or "#b38d45")
        form.label_background = str(snapshot_value("label_background", "#f7f3ec") or "#f7f3ec")
        form.logo_id = metadata.get("logo_id")
        form.project_logo_id = metadata.get("project_logo_id")
        form.logo_alignment = str(snapshot_value("logo_alignment", "left") or "left")
        form.definition_json = snapshot
        sync_form_fields(db, form, snapshot)
        db.flush()

    def create_initial_version(
        self,
        db,
        form: Form,
        *,
        actor_id: int | None,
        status: str = FORM_VERSION_DRAFT,
        technical_migration: bool = False,
    ) -> FormVersion:
        existing = self.list_versions(db, form.id)
        if existing:
            return existing[0]
        if status not in FORM_VERSION_STATUSES:
            raise FormVersionError("Nieprawidłowy status wersji formularza.")
        db.flush()
        fields = db.execute(
            select(FormField).where(FormField.form_id == form.id).order_by(FormField.sort_order, FormField.id)
        ).scalars().all()
        now = datetime.now(timezone.utc)
        version = FormVersion(
            form_id=form.id,
            version_major=1,
            version_minor=0,
            version_label="1.0",
            status=status,
            definition_json=self.build_snapshot(form, fields),
            created_by_id=actor_id,
            published_at=now if status == FORM_VERSION_PUBLISHED else None,
            published_by_id=actor_id if status == FORM_VERSION_PUBLISHED else None,
            archived_at=now if status == FORM_VERSION_ARCHIVED else None,
            change_summary=(
                "Migracja techniczna aktualnej konfiguracji formularza; wcześniejsza historia nie była dostępna."
                if technical_migration
                else "Pierwsza wersja formularza."
            ),
        )
        db.add(version)
        db.flush()
        if status == FORM_VERSION_PUBLISHED:
            try:
                ComplianceService().freeze_form_version(db, form, version, actor_id=actor_id)
            except ComplianceError as exc:
                raise FormVersionValidationError([str(exc)]) from exc
        elif status == FORM_VERSION_DRAFT:
            ComplianceService().stage_form_version(db, form, version, actor_id=actor_id)
        return version

    def sync_draft_from_legacy(
        self,
        db,
        form: Form,
        *,
        actor_id: int | None = None,
        version_id: int | None = None,
    ) -> FormVersion | None:
        draft = self.editable_draft(db, form.id, version_id=version_id)
        if not draft:
            return None
        db.flush()
        fields = db.execute(
            select(FormField).where(FormField.form_id == form.id).order_by(FormField.sort_order, FormField.id)
        ).scalars().all()
        draft.definition_json = self.build_snapshot(form, fields)
        if actor_id and not draft.created_by_id:
            draft.created_by_id = actor_id
        draft.updated_at = datetime.now(timezone.utc)
        ComplianceService().stage_form_version(db, form, draft, actor_id=actor_id)
        db.flush()
        return draft

    def update_definition(self, version: FormVersion, definition: dict, *, change_summary: str | None = None) -> None:
        if version.status != FORM_VERSION_DRAFT:
            raise FormVersionError("Można edytować wyłącznie wersję roboczą.")
        version.definition_json = deepcopy(definition or {})
        version.updated_at = datetime.now(timezone.utc)
        if change_summary is not None:
            version.change_summary = str(change_summary).strip()

    def clone_to_draft(
        self,
        db,
        form: Form,
        source_version: FormVersion,
        *,
        actor_id: int | None,
        bump: str = "minor",
    ) -> FormVersion:
        db.execute(select(Form).where(Form.id == form.id).with_for_update()).scalar_one()
        if source_version.form_id != form.id:
            raise FormVersionError("Wersja nie należy do tego formularza.")
        if source_version.status not in {FORM_VERSION_PUBLISHED, FORM_VERSION_ARCHIVED}:
            raise FormVersionError("Wersję roboczą można utworzyć tylko z wersji opublikowanej lub archiwalnej.")
        if self.editable_draft(db, form.id):
            raise FormVersionError("Formularz ma już wersję roboczą.")
        versions = self.list_versions(db, form.id)
        highest = max(((item.version_major, item.version_minor) for item in versions), default=(0, 0))
        if bump == "major":
            major, minor = highest[0] + 1, 0
        elif bump == "minor":
            major, minor = highest[0], highest[1] + 1
        else:
            raise FormVersionError("Wybierz zmianę podrzędną albo nową wersję główną.")
        draft = FormVersion(
            form_id=form.id,
            version_major=major,
            version_minor=minor,
            version_label=f"{major}.{minor}",
            status=FORM_VERSION_DRAFT,
            definition_json=deepcopy(source_version.definition_json or {}),
            created_by_id=actor_id,
            source_version_id=source_version.id,
            change_summary="",
        )
        db.add(draft)
        db.flush()
        self.apply_to_legacy_editor(db, form, draft.definition_json)
        ComplianceService().stage_form_version(db, form, draft, actor_id=actor_id)
        from services.verification_checklist_service import VerificationChecklistService
        VerificationChecklistService().clone_definitions(db, source_version.id, draft)
        return draft

    def publish(
        self,
        db,
        form: Form,
        version: FormVersion,
        *,
        actor_id: int | None,
        change_summary: str = "",
    ) -> FormVersion:
        db.execute(select(Form).where(Form.id == form.id).with_for_update()).scalar_one()
        version = db.execute(
            select(FormVersion).where(FormVersion.id == version.id).with_for_update()
        ).scalar_one()
        if version.form_id != form.id or version.status != FORM_VERSION_DRAFT:
            raise FormVersionError("Można opublikować wyłącznie wersję roboczą tego formularza.")
        expected_label = f"{version.version_major}.{version.version_minor}"
        if version.version_label != expected_label:
            raise FormVersionError("Etykieta wersji musi odpowiadać numerowi major/minor.")
        errors = validate_admin_form_config(version.definition_json or {})
        from services.verification_checklist_service import VerificationChecklistService
        errors.extend(VerificationChecklistService().validate_version(db, version))
        if not any(
            isinstance(field, dict) and field.get("name")
            for field in (version.definition_json or {}).get("fields") or []
        ):
            errors.append("Wersja nie zawiera pól formularza.")
        if errors:
            raise FormVersionValidationError(errors)

        try:
            ComplianceService().freeze_form_version(db, form, version, actor_id=actor_id)
        except ComplianceError as exc:
            raise FormVersionValidationError([str(exc)]) from exc

        now = datetime.now(timezone.utc)
        published_versions = db.execute(
            select(FormVersion)
            .where(
                FormVersion.form_id == form.id,
                FormVersion.status == FORM_VERSION_PUBLISHED,
                FormVersion.id != version.id,
            )
            .with_for_update()
        ).scalars().all()
        for previous in published_versions:
            previous.status = FORM_VERSION_ARCHIVED
            previous.archived_at = now
        db.flush()
        version.status = FORM_VERSION_PUBLISHED
        version.published_at = now
        version.published_by_id = actor_id
        version.archived_at = None
        version.change_summary = str(change_summary or version.change_summary or "").strip()
        self.apply_to_legacy_editor(db, form, version.definition_json)
        db.flush()
        self.upgrade_active_submissions(db, form, version, actor_id=actor_id)
        return version

    @staticmethod
    def is_active_submission(submission: FormSubmission) -> bool:
        from services.status_catalog import is_final_status
        from services.workflow_state_service import FinalOutcome

        outcome = str(submission.final_outcome or "").strip().upper()
        return not is_final_status(submission.process_status) and outcome in {"", FinalOutcome.ACTIVE.value}

    def upgrade_active_submissions(self, db, form: Form, version: FormVersion, *, actor_id: int | None) -> int:
        """Process IDs in bounded pages inside the caller's publication transaction."""
        upgraded = 0
        last_id = 0
        while True:
            ids = db.execute(
                select(FormSubmission.id).where(
                    FormSubmission.form_slug == form.slug,
                    FormSubmission.form_version_id.is_not(None),
                    FormSubmission.form_version_id != version.id,
                    FormSubmission.id > last_id,
                ).order_by(FormSubmission.id).limit(100)
            ).scalars().all()
            if not ids:
                break
            for submission_id in ids:
                submission = db.execute(
                    select(FormSubmission).where(FormSubmission.id == submission_id).with_for_update()
                ).scalar_one()
                if self.upgrade_submission(db, submission, version, actor_id=actor_id):
                    upgraded += 1
            last_id = ids[-1]
            db.flush()
        return upgraded

    def upgrade_submission(
        self, db, submission: FormSubmission, latest: FormVersion | None = None,
        *, actor_id: int | None = None,
    ) -> bool:
        """Reconcile one active submission before switching its immutable version pointer."""
        if not self.is_active_submission(submission):
            return False
        old_version = self.resolve_for_submission(db, submission)
        if old_version is None:
            raise FormVersionError("Zgłoszenie nie ma poprawnej historycznej wersji formularza.")
        latest = latest or self.resolve_published(db, old_version.form_id)
        if latest is None or latest.form_id != old_version.form_id or latest.status != FORM_VERSION_PUBLISHED:
            raise FormVersionError("Brak opublikowanej wersji docelowej tego formularza.")
        if old_version.id == latest.id:
            return False

        old_definition = old_version.definition_json or {}
        new_definition = latest.definition_json or {}
        from services.field_availability_service import FieldAvailabilityService
        from services.workflow_service import current_workflow_step

        availability = FieldAvailabilityService()
        old_step = current_workflow_step(submission, old_definition)
        target_step = old_step
        new_steps = set(availability.step_ids(new_definition))
        if new_steps and old_step not in new_steps:
            mapping = (new_definition.get("workflow") or {}).get("step_migration") or {}
            if not isinstance(mapping, Mapping):
                raise FormVersionValidationError(["Mapowanie usuniętych etapów workflow musi być obiektem."])
            target_step = str(mapping.get(old_step) or "").strip()
            if target_step not in new_steps:
                raise FormVersionValidationError([
                    f'Aktywne zgłoszenie jest na usuniętym etapie „{old_step}”; wskaż workflow.step_migration.{old_step}.'
                ])

        data = dict(submission.data_json or {})
        errors = self.submission_data_errors(submission, new_definition, step=target_step, db=db)
        invalid_training = self.invalid_training_selections(db, submission, new_definition)
        invalidated = self._invalidate_unsigned_documents(db, submission, old_definition, new_definition)
        rewind_step, changed_documents = self._reconcile_new_document_requirements(
            db, submission, old_definition, new_definition, target_step)
        if rewind_step:
            target_step = rewind_step
        previous_status = submission.process_status
        if rewind_step:
            target_config = next((step for step in (new_definition.get("workflow") or {}).get("steps") or []
                                  if step.get("id") == target_step), {})
            submission.process_status = str(target_config.get("status") or previous_status)
        now = datetime.now(timezone.utc)
        data["_form_version_upgrade"] = {
            "old_version_id": old_version.id,
            "new_version_id": latest.id,
            "upgraded_at": now.isoformat(),
            "missing_fields": sorted(errors),
            "invalid_training_ids": invalid_training,
            "missing_documents": [],
        }
        submission.data_json = data
        submission.form_version_id = latest.id
        if target_step != old_step or not submission.workflow_step:
            submission.workflow_step = target_step
        if target_step != old_step and (submission.workflow_stage or rewind_step):
            submission.workflow_stage = target_step
        submission.updated_at = now
        db.add(SubmissionWorkflowEvent(
            submission_id=submission.id,
            public_submission_id=submission.submission_id,
            form_slug=submission.form_slug,
            previous_status=previous_status,
            new_status=submission.process_status,
            previous_step=old_step,
            new_step=target_step,
            actor_id=actor_id,
            actor_role="admin" if actor_id else "system",
            source="form_version_upgrade",
            reason="Zgłoszenie uzgodniono z nową opublikowaną wersją formularza.",
            side_effects={
                "old_version_id": old_version.id,
                "new_version_id": latest.id,
                "old_version_label": old_version.version_label,
                "new_version_label": latest.version_label,
                "missing_fields": sorted(errors),
                "invalid_training_ids": invalid_training,
                "rewind_document_step": rewind_step,
                "changed_documents": changed_documents,
                "workflow_step_mapping": {old_step: target_step} if old_step != target_step else {},
                "superseded_file_ids": invalidated,
            },
        ))
        db.flush()
        return True

    @staticmethod
    def _reconcile_new_document_requirements(db, submission, old_definition, new_definition, current_step):
        """Reopen the earliest visited participant stage with a new signed obligation."""
        from services.documents.document_workflow_service import (has_document_workflow,
            step_documents, group_document_policy, ensure_document_item_state, document_group_completed)
        old_steps = {step.get("id"): step for step in (old_definition.get("workflow") or {}).get("steps") or []}
        new_steps = (new_definition.get("workflow") or {}).get("steps") or []
        step_order = {step.get("id"): index for index, step in enumerate(new_steps)}
        visited = set(db.execute(select(SubmissionWorkflowEvent.previous_step).where(
            SubmissionWorkflowEvent.submission_id == submission.id)).scalars())
        visited.update(db.execute(select(SubmissionWorkflowEvent.new_step).where(
            SubmissionWorkflowEvent.submission_id == submission.id)).scalars())
        if not visited:
            ids = [step.get("id") for step in new_steps]
            visited = set(ids[:ids.index(current_step)]) if current_step in ids else set()
        files = db.execute(select(SubmissionFile).where(
            SubmissionFile.submission_id == submission.id,
            SubmissionFile.status.not_in({"superseded", "rejected", "rejected_no_capacity"}))).scalars().all()
        states = deepcopy(submission.document_states or {})
        workflow_documents = states.setdefault("workflow_documents", {})
        rewind = ""
        changed_documents = []
        for step in new_steps:
            step_id = step.get("id")
            if not has_document_workflow(new_definition, step):
                continue
            old_step = old_steps.get(step_id) or {}
            old_documents = {item["id"]: item for item in step_documents(old_definition, old_step)}
            step_state = dict(workflow_documents.get(step_id) or {})
            for document in step_documents(new_definition, step):
                if not document.get("required", True):
                    continue
                previous = old_documents.get(document["id"])
                newly_required = not previous or not previous.get("required", True)
                content_changed = bool(previous and FormVersionService._document_signing_config(previous) !=
                    FormVersionService._document_signing_config(document))
                if not newly_required and not content_changed:
                    continue
                policy = group_document_policy(step, document)
                item = ensure_document_item_state(step_state, document["id"],
                    primary_document_id=str(step.get("document_id") or ""))
                matching = [file for file in files if file.document_id == document["id"] and
                            file.workflow_step_at_upload == step_id]
                satisfied = not content_changed and (bool(item.get("completed")) if policy["office_signature"] else
                    any((file.signed and file.status == "signed" if policy["participant_signature"]
                         else file.status in {"uploaded", "signed", "generated"}) for file in matching))
                if satisfied:
                    continue
                if content_changed:
                    item.clear()
                    item["minimum_file_id"] = max((file.id for file in matching), default=0)
                else:
                    item.pop("completed", None)
                step_state["completed"] = False
                step_state["substate"] = "upload"
                workflow_documents[step_id] = step_state
                changed_documents.append({"step_id": step_id, "document_id": document["id"],
                                          "reason": "content_changed" if content_changed else "new_required"})
                if (step_id != current_step and step_id in visited and not rewind and
                        step_order.get(step_id, 10**9) < step_order.get(current_step, 10**9)):
                    rewind = step_id
            if step_state and document_group_completed(step_state, new_definition, step):
                step_state["completed"] = True
                workflow_documents[step_id] = step_state
        submission.document_states = states
        return rewind, changed_documents

    @staticmethod
    def _document_signing_config(document):
        from services.documents.document_workflow_service import document_policy
        policy = document_policy({}, document)
        return {key: document.get(key) for key in ("kind", "template_source", "template_html", "template",
            "template_metadata", "builder_active_document", "builder_document", "fields", "allowed_signatures")} | {"lifecycle": {key: policy[key] for key in
            ("generate", "upload_required", "participant_signature", "office_signature")}}

    def completion_form_definition(self, submission: FormSubmission, definition: Mapping, *, step: str | None = None) -> dict:
        """Use fields available now or at an earlier stage, keeping new sections."""
        from form_loader import normalize_form_definition
        from services.field_availability_service import FieldAvailabilityService

        normalized = normalize_form_definition(dict(definition))
        availability = FieldAvailabilityService()
        steps = availability.step_ids(normalized)
        current = availability.resolve_step(normalized, step or submission.workflow_step or availability.initial_step(normalized))
        due = steps[:steps.index(current) + 1] if current in steps else [current]
        due_fields = []
        for raw in normalized.get("fields") or []:
            if not isinstance(raw, dict) or raw.get("type") == "training_selection":
                continue
            if raw.get("type") == "section":
                due_fields.append(raw)
                continue
            permissions = [availability.permission(raw, normalized, candidate) for candidate in due]
            if raw.get("type") == "static_text":
                if any(item["visible"] for item in permissions):
                    due_fields.append(raw)
                continue
            editable = [item for item in permissions if item["visible"] and item["editable"]]
            if not editable:
                continue
            field = dict(raw)
            field["required"] = any(item["required"] for item in editable)
            field["readonly"] = False
            due_fields.append(field)
        return {**normalized, "fields": availability.prune_empty_sections(due_fields), "signature": {"mode": "none"}}

    def submission_values(self, submission: FormSubmission, definition: Mapping) -> dict:
        from services.form_submission_mapper import FORM_FIELD_MAP

        values = dict(submission.data_json or {})
        for field in definition.get("fields") or []:
            name = str(field.get("name") or "")
            column = FORM_FIELD_MAP.get(name, name)
            if name and name not in values and hasattr(submission, column):
                value = getattr(submission, column)
                if value is not None:
                    values[name] = value.isoformat() if hasattr(value, "isoformat") else value
        return values

    def submission_data_errors(self, submission: FormSubmission, definition: Mapping, *, step: str | None = None,
                               db=None, values: Mapping | None = None, pending_files: set[str] | None = None) -> dict[str, str]:
        """Validate due fields with the existing availability and field validators."""
        from form_loader import validate_submission

        validation_definition = self.completion_form_definition(submission, definition, step=step)
        values = dict(values) if values is not None else self.submission_values(submission, validation_definition)
        errors = validate_submission({**validation_definition, "fields": [
            field for field in validation_definition["fields"] if field.get("type") != "file"
        ]}, values)
        if db is not None:
            from models import SubmissionFile
            from form_loader import evaluate_visible_if
            from services.submission_attachment_service import CURRENT_ATTACHMENT_STATUSES
            for raw in validation_definition["fields"]:
                if raw.get("type") != "file":
                    continue
                if not evaluate_visible_if(raw.get("visible_if"), values):
                    continue
                if not (raw.get("required") or (
                    raw.get("required_if") and evaluate_visible_if(raw.get("required_if"), values)
                )):
                    continue
                name = str(raw.get("name") or "")
                if not name or name in (pending_files or set()):
                    continue
                attached = db.execute(select(SubmissionFile.id).where(
                    SubmissionFile.submission_id == submission.id,
                    SubmissionFile.field_key == name,
                    SubmissionFile.status.in_(CURRENT_ATTACHMENT_STATUSES),
                ).limit(1)).scalar_one_or_none()
                if not attached:
                    errors[name] = "Wymagany załącznik należy uzupełnić."
        return errors

    @staticmethod
    def invalid_training_selections(db, submission: FormSubmission, definition: Mapping) -> list[str]:
        from services.training_catalog_service import TrainingCatalogService
        from services.submission_training_service import ACTIVE_STATUSES, LOCKING_STATUSES

        field = TrainingCatalogService.get_training_field(definition)
        if not field or not field.get("enabled", True):
            return []
        allowed = {item["id"] for item in TrainingCatalogService.get_trainings_for_field(field, active_only=True)}
        rows = db.execute(select(SubmissionTraining).where(SubmissionTraining.submission_id == submission.id)).scalars().all()
        invalid = [row.training_id for row in rows if row.status in ACTIVE_STATUSES
                   and not (row.is_locked or row.status in LOCKING_STATUSES) and row.training_id not in allowed]
        if field.get("required") and str(submission.process_status or "") == "TRAINING_SELECTION_OPEN" and not any(row.status in ACTIVE_STATUSES for row in rows):
            invalid.append("training_selection")
        return sorted(set(invalid))

    @staticmethod
    def _invalidate_unsigned_documents(db, submission: FormSubmission, old_definition: Mapping, new_definition: Mapping) -> list[int]:
        if old_definition == new_definition:
            return []
        files = db.execute(select(SubmissionFile).where(
            SubmissionFile.submission_id == submission.id,
            SubmissionFile.status != "superseded",
        )).scalars().all()
        invalidated = []
        now = datetime.now(timezone.utc)
        old_documents = {item.get("id"): item for item in old_definition.get("documents") or [] if isinstance(item, dict)}
        new_documents = {item.get("id"): item for item in new_definition.get("documents") or [] if isinstance(item, dict)}
        for file in files:
            custom_generated_changed = (file.document_id not in {"", "declaration", "agreement", "training_agreement"}
                and file.status == "generated" and
                FormVersionService._document_signing_config(old_documents.get(file.document_id) or {}) !=
                FormVersionService._document_signing_config(new_documents.get(file.document_id) or {}))
            if file.signed or not (file.document_type in {"form_pdf", "declaration", "agreement", "training_agreement"}
                                   or custom_generated_changed):
                continue
            file.status = "superseded"
            file.updated_at = now
            invalidated.append(file.id)
        if invalidated:
            invalidated_types = {item.document_type for item in files if item.id in invalidated}
            for training in db.execute(select(SubmissionTraining).where(
                SubmissionTraining.submission_id == submission.id,
                SubmissionTraining.agreement_file_id.in_(invalidated),
            )).scalars():
                if not training.signed_agreement_uploaded_at:
                    training.agreement_file_id = None
                    training.agreement_generated_at = None
                    training.agreement_downloaded_at = None
            if "form_pdf" in invalidated_types:
                submission.pdf_filename = ""
            if "declaration" in invalidated_types and not any(
                item.document_type == "signed_declaration" or (item.signed and item.document_type == "declaration")
                for item in files
            ):
                submission.declaration_generated = ""
                submission.declaration_filename = ""
            if invalidated_types & {"agreement", "training_agreement"}:
                try:
                    agreements = json.loads(submission.training_agreements or "[]")
                    if not isinstance(agreements, list):
                        agreements = []
                except (TypeError, ValueError):
                    agreements = []
                agreements = [item for item in agreements if isinstance(item, dict) and
                              (item.get("signature_valid") or item.get("signed"))]
                submission.training_agreements = json.dumps(
                    agreements, ensure_ascii=False
                )
                if not any(item.document_type in {"signed_agreement", "signed_training_agreement"} or
                           (item.signed and item.document_type in {"agreement", "training_agreement"}) for item in files):
                    submission.agreement_generated = ""
                    submission.agreement_filename = ""
        return invalidated

    def refresh_submission_completion(self, db, submission: FormSubmission) -> dict:
        version = self.resolve_for_submission(db, submission)
        if version is None:
            raise FormVersionError("Zgłoszenie nie ma poprawnej wersji formularza.")
        errors = self.submission_data_errors(submission, version.definition_json or {}, db=db)
        invalid_training = self.invalid_training_selections(db, submission, version.definition_json or {})
        data = dict(submission.data_json or {})
        marker = dict(data.get("_form_version_upgrade") or {})
        if marker:
            marker["missing_fields"] = sorted(errors)
            marker["invalid_training_ids"] = invalid_training
            marker["missing_documents"] = [item for item in marker.get("missing_documents") or []
                if not self._version_document_present(db, submission, item)]
            data["_form_version_upgrade"] = marker
            submission.data_json = data
        return {"errors": errors, "invalid_training_ids": invalid_training,
                "missing_documents": marker.get("missing_documents", []),
                "complete": not errors and not invalid_training and not marker.get("missing_documents")}

    @staticmethod
    def _version_document_present(db, submission, item):
        definition_id = str(item.get("document_id") or "")
        signed_required = bool(item.get("signature_required"))
        query = select(SubmissionFile.id).where(SubmissionFile.submission_id == submission.id,
            SubmissionFile.document_id == definition_id,
            SubmissionFile.workflow_step_at_upload == str(item.get("step_id") or ""),
            SubmissionFile.status.in_(["signed"] if signed_required else ["uploaded", "signed", "generated"]))
        if signed_required:
            query = query.where(SubmissionFile.signed.is_(True))
        return db.execute(query.limit(1)).scalar_one_or_none() is not None

    def require_submission_data_complete(self, db, submission: FormSubmission) -> None:
        if not self.refresh_submission_completion(db, submission)["complete"]:
            raise FormVersionError("Formularz został zaktualizowany. Przed kontynuowaniem uzupełnij wymagane dane.")

    def archive(self, db, version: FormVersion) -> None:
        if version.status != FORM_VERSION_PUBLISHED:
            raise FormVersionError("Archiwizować można wyłącznie wersję opublikowaną.")
        version.status = FORM_VERSION_ARCHIVED
        version.archived_at = datetime.now(timezone.utc)

    def delete_draft(self, db, version: FormVersion) -> None:
        if version.status != FORM_VERSION_DRAFT:
            raise FormVersionError("Usunąć można wyłącznie wersję roboczą.")
        submissions = db.execute(
            select(func.count(FormSubmission.id)).where(FormSubmission.form_version_id == version.id)
        ).scalar() or 0
        if submissions:
            raise FormVersionError("Nie można usunąć wersji powiązanej ze zgłoszeniami.")
        db.delete(version)

    def simple_diff(self, source: dict | None, target: dict | None) -> dict:
        source = source or {}
        target = target or {}
        source_fields = {
            str(field.get("name")): field
            for field in source.get("fields") or []
            if isinstance(field, dict) and field.get("name")
        }
        target_fields = {
            str(field.get("name")): field
            for field in target.get("fields") or []
            if isinstance(field, dict) and field.get("name")
        }
        source_steps = self._workflow_steps(source)
        target_steps = self._workflow_steps(target)
        return {
            "added_fields": sorted(set(target_fields) - set(source_fields)),
            "removed_fields": sorted(set(source_fields) - set(target_fields)),
            "changed_fields": sorted(
                name for name in set(source_fields) & set(target_fields)
                if source_fields[name] != target_fields[name]
            ),
            "added_workflow_steps": sorted(set(target_steps) - set(source_steps)),
            "removed_workflow_steps": sorted(set(source_steps) - set(target_steps)),
            "changed_workflow_steps": sorted(
                name for name in set(source_steps) & set(target_steps)
                if source_steps[name] != target_steps[name]
            ),
        }

    @staticmethod
    def _workflow_steps(definition: dict) -> dict[str, dict]:
        workflow = definition.get("workflow") if isinstance(definition.get("workflow"), dict) else {}
        result = {}
        for index, step in enumerate(workflow.get("steps") or []):
            if not isinstance(step, dict):
                continue
            key = str(step.get("id") or step.get("name") or index)
            result[key] = step
        return result
