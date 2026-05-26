from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)


class FormConfigService:
    def normalize_form_config(self, raw_config: dict) -> dict:
        config = deepcopy(raw_config or {})
        config["documents"] = self.normalize_documents_config(config)
        config["notifications"] = self.normalize_notifications_config(config)
        config["rules"] = self.normalize_rules_config(config)
        return self.build_default_workflow_if_missing(config)

    def list_forms(self, storage) -> list[dict]:
        forms = []
        for index, filename in enumerate(storage.list_form_files()):
            try:
                raw_config = storage.read_form_json(filename)
                form_config = self.normalize_form_config(raw_config)
                slug = Path(filename).stem
                storage.ensure_form_output_structure(slug)
                forms.append(
                    {
                        "slug": slug,
                        "title": form_config.get("title") or self.slug_to_title(slug),
                        "description": form_config.get("description", ""),
                        "definition_path": filename,
                        "tile_variant": (
                            "featured"
                            if index == 0
                            else "accent"
                            if index % 3 == 1
                            else "light"
                            if index % 3 == 2
                            else "default"
                        ),
                    }
                )
            except Exception as exc:
                logger.warning("Nie udało się załadować formularza %s: %s", filename, exc)
        return forms

    def get_form_meta(self, storage, slug: str) -> dict | None:
        for form in self.list_forms(storage):
            if form["slug"] == slug:
                return form
        return None

    def get_form_config(self, storage, slug: str) -> dict | None:
        form_meta = self.get_form_meta(storage, slug)
        if not form_meta:
            return None
        raw_config = storage.read_form_json(form_meta["definition_path"])
        return self.normalize_form_config(raw_config)

    def slug_to_title(self, slug: str) -> str:
        return slug.replace("-", " ").replace("_", " ").strip().title()

    def normalize_documents_config(self, raw_config: dict) -> list[dict]:
        documents_by_id: dict[str, dict] = {}
        document_order: list[str] = []

        process = raw_config.get("process") or {}
        process_documents = process.get("documents") if isinstance(process, Mapping) else None
        for document in self._iter_raw_documents(process_documents):
            self._merge_document(documents_by_id, document_order, document)

        raw_documents = raw_config.get("documents")
        for document in self._iter_raw_documents(raw_documents):
            self._merge_document(documents_by_id, document_order, document)

        return [self._normalize_document(documents_by_id[document_id]) for document_id in document_order]

    def _iter_raw_documents(self, raw_documents) -> list[dict]:
        if isinstance(raw_documents, list):
            return [dict(document) for document in raw_documents if isinstance(document, Mapping)]

        if isinstance(raw_documents, Mapping):
            return [
                {"id": document_id, **dict(document or {})}
                for document_id, document in raw_documents.items()
                if isinstance(document, Mapping)
            ]

        return []

    def _merge_document(self, documents_by_id: dict[str, dict], document_order: list[str], document: dict) -> None:
        document_id = str(document.get("id") or "").strip()
        if not document_id:
            invalid_key = f"__invalid_document_{len(document_order)}"
            documents_by_id[invalid_key] = dict(document)
            document_order.append(invalid_key)
            return
        if document_id not in documents_by_id:
            documents_by_id[document_id] = {"id": document_id}
            document_order.append(document_id)

        merged = documents_by_id[document_id]
        for key, value in document.items():
            if key == "id":
                continue
            if value in (None, "", [], {}):
                continue
            merged[key] = value

    def normalize_notifications_config(self, raw_config: dict) -> list[dict]:
        notifications = raw_config.get("notifications") or []
        if isinstance(notifications, Mapping):
            notifications = [dict(notification) for notification in notifications.values()]
        if not isinstance(notifications, list):
            return []
        return [dict(notification) for notification in notifications if isinstance(notification, Mapping)]

    def normalize_rules_config(self, raw_config: dict) -> list[dict]:
        rules = raw_config.get("rules") or []
        if isinstance(rules, Mapping):
            rules = [dict(rule) for rule in rules.values()]
        if not isinstance(rules, list):
            return []
        return [dict(rule) for rule in rules if isinstance(rule, Mapping)]

    def build_default_workflow_if_missing(self, form_config: dict) -> dict:
        if isinstance(form_config.get("workflow"), Mapping):
            return form_config

        document_ids = {document.get("id") for document in form_config.get("documents", [])}
        steps: list[dict[str, Any]] = [
            {"id": "submission", "type": "form_submit", "next": "officer_review"},
            {
                "id": "officer_review",
                "type": "manual_decision",
                "decisions": {
                    "accepted": (
                        "declaration"
                        if "declaration" in document_ids
                        else "agreement"
                        if "agreement" in document_ids
                        else "training_agreements"
                        if "training_agreement" in document_ids
                        else "completed"
                    ),
                    "rejected": "end_rejected",
                    "correction": "waiting_for_correction",
                },
            },
        ]

        if "declaration" in document_ids:
            steps.extend(
                [
                    {
                        "id": "declaration",
                        "type": "generate_document",
                        "document_id": "declaration",
                        "next": "declaration_signature",
                    },
                    {
                        "id": "declaration_signature",
                        "type": "signature_upload",
                        "document_id": "declaration",
                        "next": (
                            "agreement"
                            if "agreement" in document_ids
                            else "training_agreements"
                            if "training_agreement" in document_ids
                            else "completed"
                        ),
                    },
                ]
            )

        if "agreement" in document_ids:
            steps.extend(
                [
                    {
                        "id": "agreement",
                        "type": "generate_document",
                        "document_id": "agreement",
                        "next": "agreement_signature",
                    },
                    {
                        "id": "agreement_signature",
                        "type": "signature_upload",
                        "document_id": "agreement",
                        "next": "completed",
                    },
                ]
            )

        if "training_agreement" in document_ids:
            steps.extend(
                [
                    {
                        "id": "training_agreements",
                        "type": "generate_documents",
                        "document_id": "training_agreement",
                        "repeat_over": "selected_trainings",
                        "next": "training_agreements_signature",
                    },
                    {
                        "id": "training_agreements_signature",
                        "type": "signature_upload_many",
                        "document_id": "training_agreement",
                        "repeat_over": "selected_trainings",
                        "next": "completed",
                    },
                ]
            )

        steps.extend(
            [
                {"id": "waiting_for_correction", "type": "correction", "next": "officer_review"},
                {"id": "end_rejected", "type": "end"},
                {"id": "completed", "type": "end"},
            ]
        )
        form_config["workflow"] = {"initial_step": "submission", "steps": steps}
        return form_config

    def _normalize_document(self, document: Mapping[str, Any]) -> dict:
        normalized = dict(document)
        document_id = str(normalized.get("id") or "").strip()
        normalized["id"] = document_id
        normalized.setdefault("label", document_id.replace("_", " ").title())
        normalized.setdefault("kind", "generated_pdf")
        normalized.setdefault("signature_required", True)
        normalized.setdefault("allowed_signatures", ["mszafir", "profil_zaufany"])
        if "enabled" in normalized:
            normalized["enabled"] = bool(normalized.get("enabled"))
        else:
            normalized["enabled"] = True
        if normalized.get("repeat_over") and not normalized.get("repeat_item_alias"):
            normalized["repeat_item_alias"] = "item"
        return normalized
