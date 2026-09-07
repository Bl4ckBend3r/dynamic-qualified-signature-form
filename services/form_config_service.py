from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping
from form_loader import FIELD_STAGE_INITIAL
from services.field_availability_service import FieldAvailabilityService
from services.qualification_condition_service import QualificationConditionService

logger = logging.getLogger(__name__)

TRIGGER_DESCRIPTIONS = {
    "application_submitted": "Uruchamiany po wysłaniu formularza przez użytkownika.",
    "officer_accepted": "Uruchamiany po zaakceptowaniu wniosku przez urzędnika.",
    "officer_rejected": "Uruchamiany po odrzuceniu wniosku przez urzędnika.",
    "document_generated": "Uruchamiany po wygenerowaniu dokumentu.",
    "document_uploaded": "Uruchamiany po wgraniu podpisanego dokumentu przez użytkownika.",
    "contract_required": "Oznacza, że workflow wymaga obsługi umowy.",
    "declaration_required": "Oznacza, że workflow wymaga obsługi deklaracji.",
    "email_requested": "Uruchamiany, gdy workflow wymaga wysłania wiadomości e-mail.",
    "additional_fields_completed": "Uruchamiany po uzupełnieniu przez użytkownika dodatkowych pól wymaganych po akceptacji wniosku.",
    "form_draft_resume": "Wysyłany po zapisaniu wersji roboczej formularza lub ponowieniu bezpiecznego linku.",
    "repeatable_item_decision": "Wysyłany ręcznie do adresu przypisanego do konkretnego elementu grupy powtarzalnej.",
}


class FormConfigService:
    def normalize_form_config(self, raw_config: dict) -> dict:
        config = deepcopy(raw_config or {})
        config["fields"] = self.normalize_fields_config(config.get("fields") or [])
        config["documents"] = self.normalize_documents_config(config)
        config["notifications"] = self.normalize_notifications_config(config)
        config["rules"] = self.normalize_rules_config(config)
        config["qualification_conditions"] = QualificationConditionService().normalize_config(
            config.get("qualification_conditions"),
            config["fields"],
        )
        config = self.build_default_workflow_if_missing(config)
        config["workflow"] = self.normalize_workflow_config(config.get("workflow") or {})
        availability_service = FieldAvailabilityService()
        config["fields"] = [availability_service.normalize_field(field, config) for field in config["fields"]]
        config["documents"] = self.ensure_required_document_configs(config["documents"], config["workflow"])
        return config

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

    def normalize_fields_config(self, fields: list[dict]) -> list[dict]:
        if not isinstance(fields, list):
            return []
        normalized_fields = []
        for field in fields:
            if not isinstance(field, Mapping):
                continue
            item = dict(field)
            if item.get("id") and not item.get("name"):
                item["name"] = item["id"]
            if not str(item.get("stage") or "").strip():
                item["stage"] = FIELD_STAGE_INITIAL
            normalized_fields.append(item)
        return normalized_fields

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
                        "next": "office_agreement_signature",
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
                        "next": "office_agreement_signature",
                    },
                ]
            )

        if {"agreement", "training_agreement"} & document_ids:
            steps.append(
                {
                    "id": "office_agreement_signature",
                    "type": "manual_decision",
                    "label": "Umowa podpisana przez urząd",
                    "decisions": {
                        "accepted": "completed",
                        "rejected": "agreement_correction",
                        "correction": "agreement_correction",
                    },
                }
            )
            steps.append(
                {
                    "id": "agreement_correction",
                    "type": "signature_upload",
                    "document_id": "agreement",
                    "next": "office_agreement_signature",
                }
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

    def build_new_draft_workflow(self, form_config: dict) -> dict:
        """New admin drafts use business stages; legacy runtime defaults stay intact."""
        config = deepcopy(form_config)
        documents = [d for d in self.normalize_documents_config(config) if d.get("enabled", True)]
        used = {"submission", "completed"}
        steps = [{"id": "submission", "type": "form_submit", "stage_type": "user_action",
                  "admin_label": "Wniosek złożony", "user_label": "Wniosek złożony", "status": "FORM_SUBMITTED"}]
        for index, document in enumerate(documents):
            step_id = document["id"]
            if step_id in used:
                step_id = f"document_{index + 1}"
            used.add(step_id)
            steps.append({"id": step_id, "type": "document", "stage_type": "document", "document_lifecycle": "composite",
                          "document_id": document["id"], "action": "none", "status": "WAITING_FOR_DOCUMENT",
                          "admin_label": document["label"], "user_label": document["label"]})
        steps.append({"id": "completed", "type": "end", "stage_type": "final", "final": True,
                      "admin_label": "Proces zakończony", "user_label": "Proces zakończony", "status": "COMPLETED"})
        for current, following in zip(steps, steps[1:]):
            current["next"] = following["id"]
        config["workflow"] = {"schema_version": 2, "flow_mode": "explicit", "initial_step": "submission", "steps": steps}
        return config

    def normalize_workflow_config(self, workflow: Mapping[str, Any]) -> dict:
        normalized = dict(workflow or {})
        normalized.setdefault("name", normalized.get("label") or "Workflow")
        normalized["requires_declaration"] = bool(normalized.get("requires_declaration", False))
        normalized["requires_contract"] = bool(normalized.get("requires_contract", False))
        normalized.setdefault("declaration_template_html", "")
        normalized.setdefault("contract_template_html", "")
        from services.documents.document_builder_service import (
            default_document_builder_document,
            normalize_document_builder_document,
        )

        declaration_source = str(normalized.get("declaration_template_source") or "").strip().casefold()
        if not declaration_source:
            if str(normalized.get("declaration_template_html") or "").strip():
                declaration_source = "html"
            elif normalized.get("declaration_docx_template"):
                declaration_source = "docx"
            else:
                declaration_source = "builder"
        normalized["declaration_template_source"] = declaration_source if declaration_source in {"builder", "html", "docx"} else "builder"
        normalized["declaration_docx_template"] = dict(normalized.get("declaration_docx_template") or {})
        declaration_builder = normalized.get("declaration_builder_document")
        normalized["declaration_builder_document"] = (
            normalize_document_builder_document(declaration_builder, "declaration")
            if isinstance(declaration_builder, Mapping)
            else default_document_builder_document("declaration")
        )
        active_declaration_builder = normalized.get("declaration_builder_active_document")
        normalized["declaration_builder_active_document"] = (
            normalize_document_builder_document(active_declaration_builder, "declaration")
            if isinstance(active_declaration_builder, Mapping)
            else normalize_document_builder_document(normalized["declaration_builder_document"], "declaration")
        )
        source = str(normalized.get("contract_template_source") or "").strip().casefold()
        if not source:
            if str(normalized.get("contract_template_html") or "").strip():
                source = "html"
            elif normalized.get("contract_docx_template"):
                source = "docx"
            else:
                source = "builder"
        normalized["contract_template_source"] = source if source in {"builder", "html", "docx"} else "builder"
        normalized["contract_docx_template"] = dict(normalized.get("contract_docx_template") or {})
        builder_document = normalized.get("contract_builder_document")
        normalized["contract_builder_document"] = (
            normalize_document_builder_document(builder_document, "agreement")
            if isinstance(builder_document, Mapping)
            else default_document_builder_document("agreement")
        )
        active_builder_document = normalized.get("contract_builder_active_document")
        normalized["contract_builder_active_document"] = (
            normalize_document_builder_document(active_builder_document, "agreement")
            if isinstance(active_builder_document, Mapping)
            else normalize_document_builder_document(normalized["contract_builder_document"], "agreement")
        )
        normalized["contract_generation_mode"] = "per_training"
        normalized["contract_show_all_trainings_total"] = bool(
            normalized.get("contract_show_all_trainings_total", True)
        )
        normalized.setdefault("contract_filename_pattern", "")
        normalized.setdefault("contract_number_pattern", "")
        normalized.setdefault("managed_documents", False)
        steps = normalized.get("steps")
        normalized["steps"] = [dict(step) for step in steps] if isinstance(steps, list) else []
        return normalized

    def ensure_required_document_configs(self, documents: list[dict], workflow: Mapping[str, Any]) -> list[dict]:
        from services.documents.agreement_builder_service import (
            AGREEMENT_DOCUMENT_CONTEXT_VERSIONED_RUNTIME,
            resolve_agreement_document_config,
        )

        documents_by_id: dict[str, dict] = {}
        order: list[str] = []
        for index, document in enumerate(documents):
            document_id = str(document.get("id") or "").strip()
            key = document_id or f"__invalid_document_{index}"
            documents_by_id[key] = dict(document)
            order.append(key)

        requirements = [
            ("requires_declaration", "declaration", "Deklaracja", "declaration_template_html"),
            ("requires_contract", "agreement", "Umowa", "contract_template_html"),
        ]
        composite_documents = {step.get("document_id") for step in workflow.get("steps", [])
            if step.get("stage_type", step.get("type")) == "document" and step.get("document_lifecycle") == "composite"}
        for flag, document_id, label, html_key in requirements:
            if document_id not in documents_by_id and workflow.get(flag):
                documents_by_id[document_id] = {
                    "id": document_id,
                    "label": label,
                    "kind": "generated_pdf",
                    "enabled": True,
                    "signature_required": True,
                }
                order.append(document_id)
            if document_id in documents_by_id:
                # A composite stage may reference a standalone definition. Legacy
                # global controls must not disable or replace that document.
                if document_id in composite_documents and not workflow.get(flag):
                    continue
                if workflow.get("managed_documents"):
                    documents_by_id[document_id]["enabled"] = bool(workflow.get(flag))
                else:
                    documents_by_id[document_id]["enabled"] = bool(documents_by_id[document_id].get("enabled", True))
                prefix = "contract" if document_id == "agreement" else "declaration"
                workflow_controls_template = bool(
                    workflow.get("managed_documents")
                    or workflow.get(flag)
                    or str(workflow.get(html_key) or "").strip()
                    or workflow.get(f"{prefix}_docx_template")
                )
                if not workflow_controls_template:
                    continue
                template_source = str(workflow.get(f"{prefix}_template_source") or "builder")
                template_metadata = dict(workflow.get(f"{prefix}_docx_template") or {})
                agreement_config = None
                if document_id == "agreement":
                    agreement_config = resolve_agreement_document_config(
                        workflow,
                        context=AGREEMENT_DOCUMENT_CONTEXT_VERSIONED_RUNTIME,
                    )
                    template_source = agreement_config.source
                    template_metadata = dict(agreement_config.template_metadata or {})
                builder_document = None
                if template_source == "docx":
                    template_html = str(template_metadata.get("html") or "").strip()
                elif template_source == "builder":
                    from services.documents.document_builder_service import render_document_builder_template

                    builder_document = (
                        agreement_config.builder_document
                        if agreement_config is not None
                        else workflow.get(f"{prefix}_builder_active_document")
                        or workflow.get(f"{prefix}_builder_document")
                    )
                    template_html = render_document_builder_template(
                        builder_document or {},
                        document_id,
                    )
                else:
                    template_html = (
                        agreement_config.template_html
                        if agreement_config is not None
                        else str(workflow.get(html_key) or "").strip()
                    )
                if template_html:
                    documents_by_id[document_id]["template_html"] = template_html
                elif workflow.get("managed_documents"):
                    documents_by_id[document_id].pop("template_html", None)
                documents_by_id[document_id]["template_source"] = template_source
                if template_source == "builder":
                    documents_by_id[document_id]["builder_document"] = dict(builder_document or {})
                if template_source == "docx":
                    documents_by_id[document_id]["template_metadata"] = template_metadata
                    documents_by_id[document_id]["template_valid"] = bool(template_metadata.get("valid"))
                    documents_by_id[document_id]["template_unknown_variables"] = list(
                        template_metadata.get("unknown_variables") or []
                    )
                if document_id == "agreement":
                    documents_by_id[document_id]["generation_mode"] = "per_training"
                    documents_by_id[document_id]["show_all_trainings_total"] = bool(
                        workflow.get("contract_show_all_trainings_total", True)
                    )
                    filename_pattern = str(workflow.get("contract_filename_pattern") or "").strip()
                    if filename_pattern:
                        documents_by_id[document_id]["filename_pattern"] = filename_pattern
                    number_pattern = str(workflow.get("contract_number_pattern") or "").strip()
                    if number_pattern:
                        documents_by_id[document_id]["numbering"] = {"number_pattern": number_pattern}

        if workflow.get("managed_documents") and "training_agreement" in documents_by_id and "training_agreement" not in composite_documents:
            # The admin-managed agreement is the single source of truth. Per-training
            # generation is provided by AgreementFlowService's runtime adapter.
            documents_by_id["training_agreement"]["enabled"] = False

        return [self._normalize_document(documents_by_id[document_id]) for document_id in order]

    def _normalize_document(self, document: Mapping[str, Any]) -> dict:
        normalized = dict(document)
        document_id = str(normalized.get("id") or "").strip()
        normalized["id"] = document_id
        normalized.setdefault("label", document_id.replace("_", " ").title())
        normalized.setdefault("kind", "generated_pdf")
        normalized.setdefault("signature_required", True)
        normalized.setdefault("allowed_signatures", ["mszafir", "profil_zaufany"])
        if document_id == "training_agreement":
            normalized.setdefault("repeat_over", "selected_trainings")
            normalized.setdefault("repeat_item_alias", "training")
        if "enabled" in normalized:
            normalized["enabled"] = bool(normalized.get("enabled"))
        else:
            normalized["enabled"] = True
        if normalized.get("repeat_over") and not normalized.get("repeat_item_alias"):
            normalized["repeat_item_alias"] = "item"
        return normalized
