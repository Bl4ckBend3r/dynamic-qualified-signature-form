from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import func, select

from models import Form, FormField, FormSubmission, FormVersion
from services.admin_form_service import sync_form_fields, validate_admin_form_config


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

    def editable_draft(self, db, form_id: int) -> FormVersion | None:
        return db.execute(
            select(FormVersion)
            .where(FormVersion.form_id == form_id, FormVersion.status == FORM_VERSION_DRAFT)
            .order_by(FormVersion.version_major.desc(), FormVersion.version_minor.desc())
        ).scalars().first()

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
        return version

    def sync_draft_from_legacy(self, db, form: Form, *, actor_id: int | None = None) -> FormVersion | None:
        draft = self.editable_draft(db, form.id)
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
        if not any(
            isinstance(field, dict) and field.get("name")
            for field in (version.definition_json or {}).get("fields") or []
        ):
            errors.append("Wersja nie zawiera pól formularza.")
        if errors:
            raise FormVersionValidationError(errors)

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
        return version

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
