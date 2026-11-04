from __future__ import annotations

import os
from copy import deepcopy

from dotenv import load_dotenv
from sqlalchemy import select

load_dotenv(".env", override=False)

from config import Config
from database import create_session_factory
from models import Form
from services.form_version_service import FormVersionService


DEFAULT_FORM_SLUGS = ("sample_form_2",)


def _document_map(definition: dict) -> dict[str, dict]:
    result = {}

    for document in (definition or {}).get("documents") or []:
        if not isinstance(document, dict):
            continue

        document_id = str(document.get("id") or "").strip()
        if document_id:
            result[document_id] = document

    return result


def _without_allowed_signatures(definition: dict) -> dict:
    normalized = deepcopy(definition or {})

    for document in normalized.get("documents") or []:
        if isinstance(document, dict):
            document.pop("allowed_signatures", None)

    return normalized


def _is_safe_signature_policy_draft(
    published_definition: dict,
    draft_definition: dict,
) -> bool:
    if _without_allowed_signatures(
        published_definition
    ) != _without_allowed_signatures(draft_definition):
        return False

    published_documents = _document_map(published_definition)
    draft_documents = _document_map(draft_definition)

    if published_documents.keys() != draft_documents.keys():
        return False

    for document_id, published_document in published_documents.items():
        draft_document = draft_documents[document_id]

        published_has_policy = "allowed_signatures" in published_document
        draft_has_policy = "allowed_signatures" in draft_document

        if not published_has_policy:
            if draft_has_policy:
                return False
            continue

        published_allowed = published_document.get("allowed_signatures")
        draft_allowed = draft_document.get("allowed_signatures")

        if not isinstance(published_allowed, list):
            if draft_allowed != published_allowed:
                return False
            continue

        if not isinstance(draft_allowed, list):
            return False

        old_allowed = set(published_allowed)
        new_allowed = set(draft_allowed)

        if new_allowed not in (
            old_allowed,
            old_allowed | {"qualified"},
        ):
            return False

    return True


def _add_qualified_signature(definition: dict) -> tuple[dict, bool]:
    updated = deepcopy(definition or {})
    changed = False

    for document in updated.get("documents") or []:
        if not isinstance(document, dict):
            continue

        allowed = document.get("allowed_signatures")

        # Nie tworzymy automatycznie polityki tam,
        # gdzie dokument jej wcześniej nie posiadał.
        if not isinstance(allowed, list):
            continue

        if "qualified" not in allowed:
            allowed.append("qualified")
            changed = True

    return updated, changed


def _configured_slugs() -> tuple[str, ...]:
    value = os.getenv("FORM_VERSION_MIGRATION_SLUGS", "").strip()

    if not value:
        return DEFAULT_FORM_SLUGS

    return tuple(
        item.strip()
        for item in value.split(",")
        if item.strip()
    )


def main() -> int:
    Session = create_session_factory(Config.DATABASE_URL)
    service = FormVersionService()

    with Session() as db:
        for slug in _configured_slugs():
            form = db.execute(
                select(Form)
                .where(Form.slug == slug)
                .with_for_update()
            ).scalar_one_or_none()

            if form is None:
                raise RuntimeError(
                    f"Nie znaleziono formularza: {slug}"
                )

            published = service.resolve_published(db, form.id)

            if published is None:
                raise RuntimeError(
                    f"Formularz {slug} nie ma opublikowanej wersji."
                )

            _, policy_changed = _add_qualified_signature(
                published.definition_json or {}
            )

            if policy_changed:
                draft = service.editable_draft(db, form.id)

                if draft is None:
                    draft = service.clone_to_draft(
                        db,
                        form,
                        published,
                        actor_id=None,
                        bump="minor",
                    )
                else:
                    if not _is_safe_signature_policy_draft(
                        published.definition_json or {},
                        draft.definition_json or {},
                    ):
                        raise RuntimeError(
                            f"Formularz {slug} ma istniejący draft "
                            "z innymi zmianami. Automatyczne "
                            "opublikowanie zostało zablokowane."
                        )

                updated_definition, _ = _add_qualified_signature(
                    draft.definition_json or {}
                )

                service.update_definition(
                    draft,
                    updated_definition,
                    change_summary=(
                        "Dopuszczenie kwalifikowanych podpisów "
                        "elektronicznych."
                    ),
                )

                published = service.publish(
                    db,
                    form,
                    draft,
                    actor_id=None,
                    change_summary=(
                        "Dopuszczenie kwalifikowanych podpisów "
                        "elektronicznych."
                    ),
                )

                print(
                    f"{slug}: opublikowano wersję "
                    f"{published.version_label} "
                    f"(ID={published.id})"
                )
            else:
                upgraded = service.upgrade_active_submissions(
                    db,
                    form,
                    published,
                    actor_id=None,
                )

                print(
                    f"{slug}: wersja "
                    f"{published.version_label} już aktualna; "
                    f"zmigrowano aktywnych zgłoszeń: {upgraded}"
                )

        db.commit()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())