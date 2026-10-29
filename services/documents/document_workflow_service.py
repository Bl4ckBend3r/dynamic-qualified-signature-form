"""Composite document actions owned by DocumentService, using its files and renderer."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from uuid import uuid4
import json
import logging
import time
from signature_verifier import signature_verification_result


VERIFICATION_MESSAGES = {
    "VALID_SIGNATURE": "Dokument został poprawnie podpisany.",
    "INVALID_SIGNATURE": (
        "Nie udało się potwierdzić poprawności podpisu elektronicznego. Upewnij się, że wgrywasz "
        "oryginalny plik pobrany bezpośrednio po podpisaniu dokumentu. Nie otwieraj i nie zapisuj "
        "ponownie podpisanego PDF przed wgraniem."
    ),
    "NO_SIGNATURE": "PDF nie zawiera podpisu elektronicznego. Podpisz dokument i wgraj go ponownie.",
    "UNSUPPORTED_SIGNATURE": "Ten rodzaj podpisu nie jest obsługiwany dla tego dokumentu.",
    "SIGNATURE_TYPE_NOT_ALLOWED": "Ten rodzaj podpisu nie jest dozwolony dla tego dokumentu.",
    "VERIFICATION_ERROR": "Nie udało się obecnie zweryfikować podpisu. Spróbuj ponownie później.",
}

from flask import current_app

from services.upload_validation import validate_pdf_upload
from services.workflow_state_service import DocumentState
from services.training_service import parse_training_snapshots


logger = logging.getLogger(__name__)


DOCUMENT_SUBSTATES = {
    "collect_data": "Uzupełnienie danych dokumentu",
    "generating": "Generowanie dokumentu",
    "ready": "Dokument gotowy do pobrania",
    "awaiting_signature": "Podpisanie dokumentu",
    "upload": "Wgranie podpisanego dokumentu",
    "verifying": "Weryfikacja podpisu",
    "verification_failed": "Błąd weryfikacji podpisu",
    "verification_error": "Weryfikacja chwilowo niedostępna",
    "participant_signed": "Dokument podpisany przez uczestnika",
    "awaiting_office_signature": "Oczekiwanie na podpis urzędu",
    "office_signed": "Dokument podpisany przez urząd",
    "signed": "Dokument podpisany",
    "completed": "Etap dokumentowy zakończony",
    "failed": "Błąd przygotowania dokumentu",
}
STATE_CODES = {
    "generating": DocumentState.PENDING.value,
    "ready": DocumentState.READY.value,
    "awaiting_signature": DocumentState.WAITING_FOR_SIGNATURE.value,
    "upload": DocumentState.WAITING_FOR_SIGNATURE.value,
    "verification_failed": DocumentState.SIGNATURE_INVALID.value,
    "signed": DocumentState.SIGNED.value,
    "completed": DocumentState.SIGNED.value,
}

def _signature_document_message(
    *,
    signature_valid: bool,
    signature_outcome: str,
    document_status: str,
) -> str:
    if signature_valid:
        signature_message = "Podpis zweryfikowano poprawnie."
    else:
        detail = VERIFICATION_MESSAGES.get(
            signature_outcome,
            "Weryfikacja podpisu zakończyła się niepowodzeniem.",
        )
        signature_message = (
            f"Podpis nie został zweryfikowany poprawnie. {detail}"
        )

    if document_status == "VALID":
        document_message = "Dokument zweryfikowano poprawnie."
    elif document_status == "INVALID":
        document_message = (
            "Dokument jest niepoprawny. "
            "Wgrany plik nie odpowiada dokumentowi "
            "wygenerowanemu przez system."
        )
    else:
        document_message = (
            "Nie udało się zweryfikować poprawności dokumentu."
        )

    return f"{signature_message} {document_message}"


def is_document_step(step):
    """Return whether ``step`` uses the document-group runtime.

    A historical action node may still have ``type=document``.  Its explicit
    generate/await action must remain in the legacy action runtime.
    """
    from services.workflow_config_service import (
        DOCUMENTS_STAGE_TYPE,
        workflow_stage_type,
    )

    if workflow_stage_type(step) != DOCUMENTS_STAGE_TYPE:
        return False
    if str(step.get("action") or "none") != "none":
        return False
    if step.get("document_lifecycle") in {"composite", "group"}:
        return True
    raw_stage_type = str(step.get("stage_type") or "").strip()
    return raw_stage_type == DOCUMENTS_STAGE_TYPE and not step.get("explicit_stage")


def document_policy(step, document):
    policy = dict(document.get("lifecycle") or {})
    policy.update(step.get("document_options") or {})
    participant = policy.get("participant_signature", document.get("signature_required", True))
    return {
        "generate": policy.get("generate", document.get("kind", "generated_pdf") == "generated_pdf"),
        "download": policy.get("download", True),
        "participant_signature": participant,
        "office_signature": policy.get("office_signature", False),
        "signature_verification": policy.get("signature_verification", True),
        "upload_required": policy.get("upload_required", participant or document.get("kind") != "generated_pdf"),
    }


def participant_document_label(document):
    return str(document.get("participant_label") or document.get("label") or "").strip() or "Dokument"


def step_documents(definition, step):
    """Return all active documents assigned to this workflow step."""
    from services.form_config_service import FormConfigService
    from services.workflow_config_service import is_participant_stage

    step_id = str(step.get("id") or "").strip()
    if not step_id or not is_participant_stage(
        step,
        definition.get("workflow") or {},
    ):
        return []

    documents = FormConfigService().normalize_documents_config(definition)

    assigned = [
        document
        for document in documents
        if document.get("enabled", True)
        and str(document.get("workflow_step") or "").strip() == step_id
    ]

    # Legacy FormVersion: jeden dokument wskazany bezpośrednio przez step.
    legacy_document_id = str(step.get("document_id") or "").strip()
    if legacy_document_id and not any(
        str(document.get("id") or "") == legacy_document_id
        for document in assigned
    ):
        legacy = next((
            document for document in documents
            if document.get("enabled", True)
            and str(document.get("id") or "") == legacy_document_id
            and not str(document.get("workflow_step") or "").strip()
        ), None)
        if legacy:
            assigned.append(legacy)

    assigned = sorted(
        assigned,
        key=lambda document: (
            int(document.get("order") or 0),
            str(document.get("id") or ""),
        ),
    )
    from services.documents.declaration_flow_service import DeclarationFlowService
    for document in assigned:
        if str(document.get("id") or "") == "declaration":
            document["fields"] = DeclarationFlowService.submission_document_fields(
                document,
                definition,
            )
    return assigned


def group_document_policy(step, document):
    legacy_document_id = str(step.get("document_id") or "").strip()

    legacy_step_policy = (
        step
        if legacy_document_id
        and str(document.get("id") or "") == legacy_document_id
        else {}
    )

    return document_policy(legacy_step_policy, document)

def uses_document_group(definition, step):
    return is_document_step(step) and bool(step_documents(definition, step))


def has_document_workflow(definition, step):
    return uses_document_group(definition, step)


def document_states(row):
    states = row.get("document_states") or {}
    if isinstance(states, str):
        states = json.loads(states)
    return states if isinstance(states, dict) else {}


def document_step_state(row, step_id):
    return deepcopy((document_states(row).get("workflow_documents") or {}).get(step_id) or {})


def document_item_state(
    row,
    step_id,
    document_id,
    *,
    primary_document_id="",
):
    """Return state of one document inside a workflow document stage.

    New document stages always keep per-document state under
    state["documents"][document_id]. Legacy single-document stages may keep
    their primary document state directly on the workflow step.
    """
    state = document_step_state(row, step_id)
    documents = state.get("documents")

    if isinstance(documents, dict):
        item = documents.get(document_id)

        if isinstance(item, dict):
            return deepcopy(item)

    # Legacy single-document FormVersion only.
    if (
        primary_document_id
        and document_id == primary_document_id
    ):
        return deepcopy({
            key: value
            for key, value in state.items()
            if key != "documents"
        })

    return {}


def ensure_document_item_state(state, document_id, *, primary_document_id=""):
    """Return mutable state for a document, preserving legacy primary state."""
    documents = state.setdefault("documents", {})

    item = documents.get(document_id)
    if isinstance(item, dict):
        return item

    if primary_document_id and document_id == primary_document_id:
        legacy = {
            key: deepcopy(value)
            for key, value in state.items()
            if key not in {"documents", "completed"}
        }
        item = legacy
    else:
        item = {}

    documents[document_id] = item
    return item

def required_step_documents(definition, step):
    return [
        document
        for document in step_documents(definition, step)
        if document.get("required", True)
    ]


def document_group_completed(state, definition, step):
    required = required_step_documents(definition, step)

    if not required:
        return bool(step_documents(definition, step))

    documents_state = state.get("documents")
    primary_document_id = str(step.get("document_id") or "")

    if not isinstance(documents_state, dict):
        return bool(
            len(required) == 1
            and required[0].get("id") == primary_document_id
            and state.get("completed")
        )

    for document in required:
        document_id = str(document.get("id") or "")
        item_state = documents_state.get(document_id) or {}

        if item_state.get("completed"):
            continue

        # Compatibility with an already completed legacy primary document.
        if (
            document_id == primary_document_id
            and state.get("completed")
        ):
            continue

        return False

    return True

def requirements_satisfied(state, policy):
    roles = (["participant"] if policy["upload_required"] else []) + (["office"] if policy["office_signature"] else [])
    return bool(state.get("files") and roles and all(all(state.get("signatures", {}).get(f.get("training_key") or "document", {}).get(role) for role in roles) for f in state["files"]))


def document_step_status(row, step):
    state = document_step_state(row, step["id"])
    items = [item for item in (state.get("documents") or {}).values()
             if isinstance(item, dict)]
    if items:
        item_substates = [str(item.get("substate") or "") for item in items]
        substate = next((candidate for candidate in (
            "verification_error", "verification_failed", "failed", "collect_data",
            "generating", "awaiting_office_signature", "ready", "upload",
        ) if candidate in item_substates), "")
        if not substate:
            if all(item.get("completed") for item in items):
                substate = "completed"
            elif any(item.get("uploaded_filename") and not item.get("completed") for item in items):
                substate = "awaiting_office_signature"
            elif any(item.get("generated_filename") for item in items):
                substate = "ready"
            else:
                substate = "upload"
    else:
        substate = state.get("substate", "generating")
    label = (
            step.get("label")
            or step.get("user_label")
            or step.get("admin_label")
            or "Dokument"
        )
    messages = {
        "collect_data": "Uzupełnij dane wymagane do przygotowania deklaracji.",
        "verification_error": "Nie udało się obecnie zweryfikować podpisu. Spróbuj ponownie później.",
        "generating": "Przygotowujemy dokument.", "ready": "Dokument jest gotowy do pobrania.",
        "awaiting_signature": "Podpisz pobrany dokument elektronicznie.",
        "upload": "Wgraj dokument.", "verifying": "Weryfikujemy podpis dokumentu.",
        "verification_failed": "Nie udało się potwierdzić prawidłowego podpisu. Wgraj dokument ponownie.",
        "awaiting_office_signature": "Dokument oczekuje na podpis urzędu.",
        "participant_signed": "Dokument został podpisany przez uczestnika.",
        "office_signed": "Dokument został podpisany przez urząd.",
        "signed": "Dokument został poprawnie podpisany.", "completed": "Dokument został obsłużony.",
        "failed": "Nie udało się przygotować dokumentu. Spróbuj ponownie.",
    }
    next_actions = {
        "generating": "Poczekaj na przygotowanie dokumentu.",
        "ready": "Pobierz dokument, podpisz go elektronicznie i wgraj podpisany plik.",
        "awaiting_signature": "Pobierz dokument, podpisz go elektronicznie i wgraj podpisany plik.",
        "upload": "Wgraj podpisany dokument.",
        "verifying": "Poczekaj na zakończenie weryfikacji podpisu.",
        "verification_failed": "Popraw podpis i wgraj dokument ponownie.",
    }
    message = messages.get(substate, "Przygotowujemy dokument.")
    if substate in {"verification_failed", "verification_error"}:
        message = VERIFICATION_MESSAGES.get(state.get("verification_result"), message)
    return {
            "title": f"{label} — {DOCUMENT_SUBSTATES.get(substate, 'Przygotowanie dokumentu').lower()}",
            "message": message,
            "next_action": next_actions.get(substate, ""),
            "substate": substate,
            "variant": "danger" if substate in {
                "failed",
                "verification_failed",
                "verification_error",
            } else "info",
        }


def validate_document_step(step, form_config):
    documents = step_documents(form_config, step)
    if not documents:
        return ["Etap dokumentowy musi mieć przypisany co najmniej jeden włączony dokument."]
    errors = []
    if not step.get("next") or step.get("final") or step.get("transitions"):
        errors.append("Złożony etap dokumentowy wymaga jednego kolejnego etapu.")
    if not isinstance(step.get("document_options", {}), dict):
        return [*errors, "Przebieg dokumentu musi być obiektem konfiguracji."]
    waiting_for_collection_selection = False

    for document in documents:
        policy = group_document_policy(step, document)
        if not isinstance(document.get("required", True), bool) or not isinstance(document.get("signature_required", True), bool):
            errors.append(f"Dokument {document['id']} wymaga logicznych ustawień wymagania i podpisu.")
        if any(not isinstance(value, bool) for value in policy.values()):
            errors.append(f"Dokument {document['id']} ma nieprawidłowe ustawienia przebiegu.")
        if policy["participant_signature"] and not policy["upload_required"]:
            errors.append(f"Dokument {document['id']}: podpis uczestnika wymaga uploadu.")
        if policy["office_signature"] and not policy["participant_signature"]:
            errors.append(f"Dokument {document['id']}: podpis urzędu wymaga podpisu uczestnika.")
        if not policy["generate"] and not policy["upload_required"]:
            errors.append(f"Dokument {document['id']} wymaga generowania lub uploadu.")
        if policy["generate"] and not any(document.get(key) for key in
                ("template", "template_html", "builder_document", "template_metadata")):
            errors.append(f"Dokument {document['id']} wymaga szablonu.")
    instructions = step.get("document_instructions", {})
    if not isinstance(instructions, dict) or any(key not in DOCUMENT_SUBSTATES or not isinstance(value, dict) for key, value in instructions.items()):
        errors.append("Instrukcje dokumentu wskazują nieobsługiwany podetap lub mają błędny format.")
    return errors


class DocumentWorkflowService:
    def __init__(self, documents):
        self.documents = documents

    def _training_name(self, row, source):
        training_key = str(source.get("training_key") or "").strip()
        if not training_key:
            return ""
        session_factory = getattr(self.repository, "session_factory", None)
        if session_factory and row.get("id"):
            try:
                from models import SubmissionTraining
                with session_factory() as db:
                    training = db.query(SubmissionTraining).filter_by(
                        submission_id=row["id"], training_id=training_key
                    ).one_or_none()
                    if training:
                        snapshot = dict(training.training_snapshot or {})
                        name = str(training.training_name_snapshot or snapshot.get("name") or "").strip()
                        if name:
                            return name
            except Exception:
                logger.warning("training_name_snapshot_lookup_failed", exc_info=True)
        for item in parse_training_snapshots(row.get("selected_trainings")):
            if str(item.get("id") or "") == training_key:
                name = str(item.get("name") or "").strip()
                if name:
                    return name
        return str(source.get("training_name") or source.get("label") or "").strip()

    def _public_file_label(self, row, document, source):
        if str(document.get("id") or "") not in {"agreement", "training_agreement"}:
            name = participant_document_label(document)
            if str(document.get("participant_label") or "").strip():
                instance_label = str(source.get("label") or "").strip()
                generic_label = str(document.get("label") or "").strip()
                return f"{name} — {instance_label}" if instance_label and instance_label not in {name, generic_label} else name
            return str(source.get("label") or name)
        training_name = self._training_name(row, source)
        return f"Umowa - {training_name}" if training_name else "Umowa"

    def training_selection_changed(self, db, submission, definition, field):
        """Retire a dependent declaration using the existing revision and signature history."""
        from datetime import datetime, timezone
        from jinja2 import meta, nodes
        from models import SubmissionFile
        from services.training_service import parse_training_snapshots
        selected = {item["id"]: item for item in parse_training_snapshots(submission.selected_trainings)}
        states = deepcopy(submission.document_states or {})
        for step in definition.get("workflow", {}).get("steps", []):
            if not is_document_step(step):
                continue
            collection = next((item for item in step_documents(definition, step)
                               if self._is_collection_document(item)), {})
            if collection.get("repeat_over") != "selected_trainings" and collection.get("generation_mode") != "per_training":
                continue
            state = (states.get("workflow_documents") or {}).get(step["id"])
            if not state:
                continue
            grouped = uses_document_group(definition, step)
            target = (state.get("documents") or {}).get(collection["id"], {}) if grouped else state

            files = {
                file.get("training_key"): file
                for file in target.get("files") or []
            }

            if set(files) == set(selected) and target.get("substate") != "failed":
                continue

            target["files"] = [
                files.get(key) or {
                    "filename": "",
                    "training_key": key,
                    "label": item["name"],
                }
                for key, item in selected.items()
            ]

            target["completed"] = False
            target["substate"] = "ready"
            target["data_confirmed"] = True

            state["completed"] = False
            state["substate"] = "ready"
            state["status"] = DocumentState.READY.value
        submission.document_states = states
        document = self.documents.get_document_by_id(definition, "declaration")
        if not document:
            return
        declaration_steps = [
            step
            for step in definition.get("workflow", {}).get("steps", [])
            if any(
                item.get("id") == "declaration"
                for item in step_documents(definition, step)
            )
        ]
        declaration_states = []
        for step in declaration_steps:
            state = (
                (states.get("workflow_documents") or {})
                .get(step["id"], {})
            )

            documents_state = state.get("documents") or {}
            item = documents_state.get("declaration")

            # Kompatybilność historycznych FormVersion, które przechowywały
            # pojedynczą deklarację bezpośrednio w stanie etapu.
            if item is None and str(step.get("document_id") or "") == "declaration":
                item = state

            declaration_states.append(item or {})
        if not (str(submission.declaration_generated or "").lower() == "tak"
                or (submission.data_json or {}).get("_signed_declaration_snapshot")
                or any(state.get("files") or state.get("data_confirmed") for state in declaration_states)):
            return
        template_source = str(document.get("template_source") or "").strip().casefold()
        has_template = bool(
            str(document.get("template_html") or "").strip()
            or str(document.get("template") or "").strip()
            or (template_source == "builder" and document.get("builder_document"))
            or (template_source == "docx" and document.get("template_metadata"))
        )
        if not has_template:
            # A training-only form can carry the declaration document container
            # without having a declaration template or generated file to retire.
            return
        template = self.documents.resolve_document_template(document)
        parsed = current_app.jinja_env.parse(template or "")
        references = meta.find_undeclared_variables(parsed)
        training_names = {str(field.get("name") or "selected_trainings"), "selected_trainings",
            "selected_trainings_normalized", "selected_trainings_total", "selected_trainings_total_formatted",
            "all_selected_trainings", "all_selected_trainings_total", "all_selected_trainings_total_formatted"}
        aggregates = {"submission", "data_json", "submission_view", "fields"}
        accesses = list(parsed.find_all((nodes.Getattr, nodes.Getitem)))
        accessed_nodes = {id(node.node) for node in accesses}
        aggregate_use = any(node.name in aggregates and id(node) not in accessed_nodes
                            for node in parsed.find_all(nodes.Name))
        dependent_access = False
        for access in accesses:
            root = access.node
            while isinstance(root, (nodes.Getattr, nodes.Getitem)):
                root = root.node
            if not isinstance(root, nodes.Name) or root.name not in aggregates:
                continue
            key = access.attr if isinstance(access, nodes.Getattr) else access.arg.value if isinstance(access.arg, nodes.Const) else None
            dependent_access |= key is None or key in training_names
        # Whole/dynamic context access can contain training fields; ordinary
        # references such as submission.first_name do not invalidate a signature.
        if not (references.intersection(training_names) or aggregate_use or dependent_access):
            return
        if float(states.get("document_operation_until") or 0) > time.time():
            raise ValueError("Dokument jest już przetwarzany. Odśwież status za chwilę.")
        steps = declaration_steps
        data = dict(submission.data_json or {})
        snapshot = data.pop("_signed_declaration_snapshot", None)
        now = datetime.now(timezone.utc)
        if snapshot:
            data["_declaration_signature_history"] = [*data.get("_declaration_signature_history", []),
                {**snapshot, "invalidated_at": now.isoformat(), "reason": "training_selection_changed"}]
            submission.data_json = data
        affected = False
        for step in steps:
            state = (
                (states.get("workflow_documents") or {})
                .get(step["id"], {})
            )

            documents_state = state.get("documents") or {}
            item = documents_state.get("declaration")

            # Legacy: starsze wersje trzymały stan deklaracji bezpośrednio
            # na poziomie workflow_documents[step_id].
            if item is None and str(step.get("document_id") or "") == "declaration":
                item = state

            item = item or {}

            if (
                not item.get("files")
                and not item.get("data_confirmed")
                and not item.get("generated_filename")
            ):
                continue
            affected = True
            replacement = {"document_id": "declaration", "substate": "collect_data",
                "status": DocumentState.PENDING.value, "data_confirmed": False, "regenerate": True}
            if str(step.get("document_id") or "") == "declaration" and not state.get("documents"):
                # Legacy state pozostawiamy w dotychczasowym formacie.
                state.update(replacement)
            else:
                # Nowy model: zawsze per-document nested state.
                state.setdefault("documents", {})["declaration"] = replacement
        if affected or snapshot or str(submission.declaration_generated or "").lower() == "tak":
            for file in db.query(SubmissionFile).filter(SubmissionFile.submission_id == submission.id,
                    SubmissionFile.document_id == "declaration", SubmissionFile.status != "superseded").all():
                file.status, file.updated_at = "superseded", now
            for key in ("declaration_generated", "declaration_filename", "declaration_signed",
                        "declaration_signed_filename", "declaration_signature_valid", "declaration_signature_type"):
                setattr(submission, key, "")
            submission.declaration_signature_error = "Wybór szkoleń zmienił treść deklaracji. Wygeneruj i podpisz nową deklarację."
            states["declaration"] = DocumentState.PENDING.value
            submission.document_states = states

    @property
    def repository(self):
        return self.documents.submission_repository


    def _resolve_step_document(self, definition, step, document_id=None):
        documents = step_documents(definition, step)

        target_document_id = str(document_id or "").strip()

        if not target_document_id:
            # Legacy single-document FormVersion.
            target_document_id = str(
                step.get("document_id") or ""
            ).strip()

        if not target_document_id:
            raise ValueError("Nie wskazano dokumentu.")

        document = next(
            (
                item
                for item in documents
                if str(item.get("id") or "") == target_document_id
            ),
            None,
        )

        if document is None:
            raise ValueError(
                "Dokument nie jest dostępny na tym etapie."
            )

        return document

    def _current(
        self,
        submission,
        definition,
        expected_step=None,
        *,
        document_id=None,
    ):
        row = self.repository.get_by_id(
            self.documents._submission_id(submission)
        )

        if not row:
            raise ValueError("Nie znaleziono zgłoszenia.")

        current = row.get("workflow_step") or row.get("workflow_stage")

        step = next(
            (
                item
                for item in definition.get("workflow", {}).get("steps", [])
                if item.get("id") == current
            ),
            {},
        )

        if not has_document_workflow(definition, step) or (
            expected_step and current != expected_step
        ):
            raise ValueError("Etap dokumentowy nie jest już aktywny.")

        if not step_documents(definition, step):
            raise ValueError("Etap dokumentowy nie ma przypisanych dokumentów.")

        document = None

        if document_id:
            document = self._resolve_step_document(
                definition,
                step,
                document_id,
            )
        elif step.get("document_id"):
            # Legacy FormVersion.
            document = self._resolve_step_document(
                definition,
                step,
            )

        return row, step, document

    def _claim(self, row, step):
        if not hasattr(self.repository, "claim_document_operation"):
            raise ValueError("Złożone etapy dokumentowe wymagają trwałej bazy metadanych.")
        token = uuid4().hex
        if not self.repository.claim_document_operation(row["submission_id"], step["id"], token):
            raise ValueError("Dokument jest już przetwarzany. Odśwież status za chwilę.")
        return token

    def _save(self, row, step, state, token, substate, event, actor="system", *, field_values=None):
        state["substate"] = substate
        state["status"] = STATE_CODES.get(substate, DocumentState.PENDING.value)
        if substate == "completed" and not state.get("signature_required", True):
            state["status"] = DocumentState.READY.value
        legacy_document_id = str(step.get("document_id") or "").strip()

        if legacy_document_id and not state.get("documents"):
            # Kompatybilność starego single-document state.
            state["document_id"] = legacy_document_id
        if not self.repository.save_document_step(row["submission_id"], step["id"], state, token, event, actor, field_values=field_values):
            raise ValueError("Etap zmienił się podczas obsługi dokumentu.")

    def _files(self, row, step, document_id=None):
        target_document_id = str(
            document_id or ""
        ).strip()

        if not target_document_id:
            # Legacy FormVersion.
            target_document_id = str(
                step.get("document_id") or ""
            ).strip()

        if not target_document_id:
            return []

        return [
            file
            for file in self.documents.submission_document_service.list_documents(
                row["submission_id"]
            )
            if file.get("document_id") == target_document_id
            and file.get("workflow_step_at_upload") == step["id"]
            and file.get("status")
            not in {
                "superseded",
                "rejected",
                "rejected_no_capacity",
            }
        ]

    def _document_state(self, row, step, document):
        step_state = document_step_state(row, step["id"])

        item_state = ensure_document_item_state(
            step_state,
            document["id"],
            primary_document_id=str(step.get("document_id") or ""),
        )

        return step_state, item_state

    @staticmethod
    def needs_data(document, state):
        from services.documents.declaration_template_context_service import declaration_document_fields

        fields = document.get("fields") or []
        if document.get("id") == "declaration":
            fields = declaration_document_fields(fields)
        return (any(f.get("name") and f.get("type") not in {"section", "static_text"} for f in fields)
                and not state.get("data_confirmed") and not state.get("completed")
                and not any(state.get("signatures", {}).values()))

    def save_fields(self, submission, definition, step_id, form_data, *, document_id=None, generate=True,):
        row, step, document = self._current(
            submission,
            definition,
            step_id,
            document_id=document_id,
        )

        if document is None:
            raise ValueError(
                "Nie wskazano dokumentu do zapisania danych."
            )

        token = self._claim(row, step)

        try:
            row = self.repository.get_by_id(row["submission_id"])
            step_state = document_step_state(row, step_id)

            state = ensure_document_item_state(
                step_state,
                document["id"],
                primary_document_id=str(step.get("document_id") or ""),
            )

            if (
                state.get("completed")
                or state.get("uploaded_filename")
                or state.get("office_filename")
                or (
                    state.get("data_confirmed")
                    and (
                        state.get("files")
                        or state.get("generated_filename")
                    )
                )
                or any(state.get("signatures", {}).values())
            ):
                raise ValueError(
                    "Dane wygenerowanego dokumentu są zablokowane. "
                    "Zmiana wymaga korekty dokumentu."
                )

            flow, data = current_app.extensions[
                "services"
            ].declaration_flow_service.validate_document_fields(
                document,
                row,
                form_data,
                self.repository,
            )

            if not flow.success:
                return flow

            current_app.extensions[
                "services"
            ].declaration_flow_service.save_document_training_selection(
                document,
                row,
                form_data,
                current_app.extensions["services"],
            )

            saved_row = self.repository.get_by_id(row["submission_id"])

            for field in flow.declaration_definition["fields"]:
                if field.get("type") == "training_selection":
                    data[field["name"]] = (
                        saved_row.get("selected_trainings") or "[]"
                    )

            state["data_confirmed"] = generate

            if generate:
                state["data_sha256"] = sha256(
                    json.dumps(
                        data,
                        sort_keys=True,
                        ensure_ascii=False,
                    ).encode()
                ).hexdigest()

                for file in self._files(
                    row,
                    step,
                    document["id"],
                ):
                    if not file.get("signed"):
                        if not self.repository.record_file(
                            row["submission_id"],
                            {**file, "status": "superseded"},
                        ):
                            raise RuntimeError(
                                "Nie udało się zastąpić poprzedniej wersji dokumentu."
                            )

                state.pop("generated_filename", None)
                state["regenerate"] = True

            state["substate"] = "collect_data"

            self._save(
                row,
                step,
                step_state,
                token,
                "collect_data",
                "DOCUMENT_FIELDS_SAVED",
                "participant",
                field_values=data,
            )

        finally:
            self.repository.release_document_operation(
                row["submission_id"],
                token,
            )

        qualification = current_app.extensions["services"].submission_service.evaluate_saved_document(
            row["submission_id"], definition, document["id"],
        )
        if any(item.get("failure_action") in {"auto_reject", "officer_decision"}
               for item in qualification.get("failed_conditions") or []):
            return flow

        if generate:
            self.documents.workflow_service.run_automatic_steps(
                self.repository.get_by_id(row["submission_id"]),
                definition,
            )

        return flow

    @staticmethod
    def _group_satisfied(state, documents, step):
        items = state.get("documents") or {}
        return bool(documents) and all(
            not document.get("required", True) or bool((items.get(document["id"]) or {}).get("completed"))
            for document in documents
        )

    @staticmethod
    def _is_collection_document(document):
        return bool(document.get("repeat_over") or document.get("generation_mode") == "per_training")

    def _prepare_group_collection(self, row, step, definition, document, item, policy):
        collection_field = document.get("repeat_over") or "selected_trainings"
        if policy["generate"]:
            records = self.documents.generate_documents_for_collection(
                row, definition, document["id"], collection_field,
                document.get("repeat_item_alias") or "training")
        else:
            records = [{"id": source["id"], "training_name": source.get("name") or source["id"],
                        "filename": ""} for source in parse_training_snapshots(row.get(collection_field))]
        if not records:
            raise ValueError("Brak danych wymaganych do wygenerowania dokumentu.")
        instances = item.setdefault("instances", {})
        item["files"] = []
        for record in records:
            key = str(record.get("id") or record.get("training_id") or "")
            if not key:
                raise ValueError("Dokument szkolenia nie ma identyfikatora.")
            source = instances.setdefault(key, {})
            source["generated_filename"] = record.get("filename") or ""
            if not policy["upload_required"] and not policy["office_signature"]:
                source["completed"] = True
            item["files"].append({"training_key": key, "filename": source["generated_filename"],
                                  "label": record.get("training_name") or record.get("name") or key})
        item["generated_filename"] = item["files"][0]["filename"]
        item["completed"] = all(instances[file["training_key"]].get("completed") for file in item["files"])
        item["substate"] = "completed" if item["completed"] else "ready" if policy["generate"] else "upload"
        item.pop("regenerate", None)
        return records

    def _start_group(self, row, step, definition):
        documents = step_documents(definition, step)
        token = self._claim(row, step)
        context_token = self.documents._document_operation_token.set(token)
        try:
            row = self.repository.get_by_id(row["submission_id"])
            state = document_step_state(row, step["id"])
            for document in documents:
                ensure_document_item_state(state, document["id"],
                    primary_document_id=str(step.get("document_id") or ""))
            if state.get("completed"):
                if self._group_satisfied(state, documents, step):
                    return True
                state["completed"] = False
            items = state["documents"]
            for document in documents:
                item = items[document["id"]]
                policy = group_document_policy(step, document)
                existing = [file for file in self.documents.submission_document_service.list_documents(row["submission_id"])
                            if file.get("document_id") == document["id"] and file.get("workflow_step_at_upload") == step["id"]
                            and int(file.get("id") or 0) > int(item.get("minimum_file_id") or 0)
                            and file.get("status") not in {"superseded", "rejected", "rejected_no_capacity"}]
                if item.get("completed") and existing:
                    continue
                if item.get("completed") and not existing:
                    item.pop("completed", None)
                    item.pop("generated_filename", None)
                signed = next((file for file in reversed(existing) if file.get("signed")), None)
                if signed:
                    item.update(uploaded_filename=signed["filename"],
                                completed=not policy["office_signature"])
                    item["substate"] = "awaiting_office_signature" if policy["office_signature"] else "completed"
                    continue
                if self.needs_data(document, item):
                    item["substate"] = "collect_data"
                    continue
                if self._is_collection_document(document):
                    collection_field = document.get("repeat_over") or "selected_trainings"

                    if not parse_training_snapshots(row.get(collection_field)):
                        item["files"] = []
                        item.pop("generated_filename", None)
                        item["completed"] = False
                        item["substate"] = "ready"
                        continue

                    try:
                        self._prepare_group_collection(
                            row,
                            step,
                            definition,
                            document,
                            item,
                            policy,
                        )
                    except Exception:
                        item["substate"] = "failed"
                        item["data_confirmed"] = False
                        self._save(
                            row,
                            step,
                            state,
                            token,
                            "failed",
                            "DOCUMENT_GENERATION_FAILED",
                        )
                        raise

                    continue
                if item.get("generated_filename") and any(file.get("filename") == item["generated_filename"]
                    for file in existing):
                    continue
                item.pop("generated_filename", None)
                if not policy["generate"]:
                    item["substate"] = "upload"
                    continue
                generated = next((file for file in reversed(existing) if file.get("status") == "generated"), None)
                if not generated:
                    try:
                        generated = self.documents.generate_document(row, definition, document["id"])
                    except Exception:
                        item["substate"] = "failed"
                        item["data_confirmed"] = False
                        self._save(row, step, state, token, "failed", "DOCUMENT_GENERATION_FAILED")
                        raise
                item["generated_filename"] = generated["filename"]
                item["substate"] = "ready"
                item.pop("regenerate", None)
                if not policy["upload_required"] and not policy["office_signature"]:
                    item["completed"] = True
                    item["substate"] = "completed"
            state["signature_required"] = any(group_document_policy(step, item)["participant_signature"] for item in documents)
            if self._group_satisfied(state, documents, step):
                self._complete(row, step, state, token)
                return True
            self._save(row, step, state, token, "upload", "DOCUMENT_GROUP_READY")
            return False
        finally:
            self.documents._document_operation_token.reset(context_token)
            self.repository.release_document_operation(row["submission_id"], token)

    def generate_group_document(self, submission, definition, step_id, document_id):
        row, step, document = self._current(submission, definition, step_id, document_id=document_id)
        if not uses_document_group(definition, step) or not group_document_policy(step, document)["generate"]:
            raise ValueError("Generowanie tego dokumentu jest wyłączone.")
        token = self._claim(row, step)
        context_token = self.documents._document_operation_token.set(token)
        try:
            row = self.repository.get_by_id(row["submission_id"])
            state = document_step_state(row, step_id)
            item = ensure_document_item_state(state, document_id,
                primary_document_id=str(step.get("document_id") or ""))
            if state.get("completed") or item.get("completed"):
                raise ValueError("Dokument został już ukończony.")
            if self.needs_data(document, item):
                raise ValueError("Najpierw uzupełnij dane dokumentu.")
            if not self._is_collection_document(document) and (item.get("uploaded_filename") or
                    any(file.get("signed") for file in self._files(row, step, document_id))):
                raise ValueError("Podpisany dokument jest już zapisany.")
            policy = group_document_policy(step, document)
            try:
                if self._is_collection_document(document):
                    records = self._prepare_group_collection(row, step, definition, document, item, policy)
                    existing = {"filename": records[0]["filename"], "document_id": document_id}
                else:
                    existing = next((file for file in reversed(self._files(row, step, document_id))
                                     if file.get("status") == "generated" and
                                     int(file.get("id") or 0) > int(item.get("minimum_file_id") or 0)), None)
                    if not existing:
                        existing = self.documents.generate_document(row, definition, document_id)
                    item["generated_filename"] = existing["filename"]
                    item["substate"] = "ready"
                    item.pop("regenerate", None)
                    if not policy["upload_required"] and not policy["office_signature"]:
                        item["completed"] = True
                        item["substate"] = "completed"
            except Exception:
                item["substate"] = "failed"
                item["data_confirmed"] = False
                self._save(row, step, state, token, "failed", "DOCUMENT_GENERATION_FAILED")
                raise
            if self._group_satisfied(state, step_documents(definition, step), step):
                self._complete(row, step, state, token)
                completed = True
            else:
                self._save(row, step, state, token, "upload", "DOCUMENT_GROUP_FILE_GENERATED", "participant")
                completed = False
        finally:
            self.documents._document_operation_token.reset(context_token)
            self.repository.release_document_operation(row["submission_id"], token)
        if completed:
            self.documents.workflow_service.advance_after_action(self.repository.get_by_id(row["submission_id"]), definition)
        return existing

    def _upload_group(self, submission, definition, step_id, uploaded_file, document_id, signer="participant", instance=""):
        if not uploaded_file or not uploaded_file.filename:
            raise ValueError("Wybierz plik dokumentu.")
        current = self.repository.get_by_id(
            self.documents._submission_id(submission)
        )
        configured_step = next(
            (
                candidate
                for candidate in definition.get("workflow", {}).get("steps", [])
                if candidate.get("id") == step_id
            ),
            {},
        )
        requested_document_id = str(
            document_id or configured_step.get("document_id") or ""
        ).strip()
        if current and (current.get("workflow_step") or current.get("workflow_stage")) != step_id:
            document = self._resolve_step_document(
                definition,
                configured_step,
                requested_document_id,
            )
            state = document_step_state(current, step_id)
            item = document_item_state(
                current,
                step_id,
                document["id"],
                primary_document_id=str(configured_step.get("document_id") or ""),
            )
            target = (
                (item.get("instances") or {}).get(instance, {})
                if self._is_collection_document(document)
                else item
            )
            if state.get("completed") and target.get("completed"):
                accepted_filename = (
                    target.get("office_filename")
                    if signer == "office"
                    else target.get("uploaded_filename")
                )
                data = uploaded_file.read()
                metadata = self.documents.submission_document_service.list_documents(
                    current["submission_id"]
                )
                if any(
                    file.get("filename") == accepted_filename
                    and file.get("checksum_sha256") == sha256(data).hexdigest()
                    for file in metadata
                ):
                    return {
                        "is_valid": True,
                        "completed": True,
                        "result": "VALID_SIGNATURE",
                        "message": VERIFICATION_MESSAGES["VALID_SIGNATURE"],
                    }
            raise ValueError("Etap dokumentowy nie jest już aktywny.")
        row, step, _ = self._current(
            submission,
            definition,
            step_id,
        )

        document_id = str(document_id or "").strip()

        if not document_id:
            # Legacy single-document FormVersion.
            document_id = str(step.get("document_id") or "").strip()

        if not document_id:
            raise ValueError("Nie wskazano dokumentu.")

        document = self._resolve_step_document(
            definition,
            step,
            document_id,
        )
        documents = step_documents(definition, step)
        collection = self._is_collection_document(document)
        if collection and not instance or not collection and instance:
            raise ValueError("Wskaż właściwy dokument szkolenia.")
        policy = group_document_policy(step, document)
        if signer not in {"participant", "office"} or not (policy["office_signature"] if signer == "office" else policy["upload_required"]):
            raise ValueError("Wgrywanie tego dokumentu jest wyłączone.")
        token = self._claim(row, step)
        completed = False
        try:
            state = document_step_state(self.repository.get_by_id(row["submission_id"]), step_id)
            if state.get("completed"):
                raise ValueError("Etap dokumentowy został już zakończony.")
            item = ensure_document_item_state(
                state,
                document_id,
                primary_document_id=str(step.get("document_id") or ""),
            )
            source = next((file for file in item.get("files") or [] if file.get("training_key") == instance), None) if collection else None
            if collection and not source:
                raise ValueError("Dokument nie należy do wybranego szkolenia.")
            target = item.setdefault("instances", {}).setdefault(instance, {}) if collection else item
            if signer == "participant" and policy["generate"] and not target.get("generated_filename"):
                raise ValueError("Najpierw wygeneruj dokument do podpisu.")
            office_source = target.get("uploaded_filename") or (
                target.get("generated_filename") if not policy["upload_required"] else "")
            if target.get("completed") or signer == "office" and (not office_source or target.get("office_filename")):
                raise ValueError("Ten dokument został już przyjęty.")
            metadata = self.documents.submission_document_service.list_documents(row["submission_id"])
            data = uploaded_file.read()
            existing_signed = next((file for file in reversed(metadata)
                if signer == "participant" and file.get("document_id") == document_id and file.get("workflow_step_at_upload") == step_id
                   and (not collection or file.get("training_key") == instance)
                   and int(file.get("id") or 0) > int(item.get("minimum_file_id") or 0)
                   and file.get("signed") and file.get("status") == "signed"), None)
            if existing_signed:
                if existing_signed.get("checksum_sha256") != sha256(data).hexdigest():
                    raise ValueError("Podpisany dokument jest już zapisany.")
                target.update(
                    uploaded_filename=existing_signed["filename"],
                    completed=not policy["office_signature"],
                    substate=("awaiting_office_signature" if policy["office_signature"] else "completed"),
                )
                if collection:
                    item["completed"] = bool(item.get("files")) and all(
                        (item.get("instances") or {}).get(file["training_key"], {}).get("completed")
                        for file in item["files"])
                completed = self._group_satisfied(state, documents, step)
                if completed:
                    self._complete(row, step, state, token)
                else:
                    self._save(row, step, state, token, target["substate"], "DOCUMENT_GROUP_FILE_RECOVERED", signer)
                self.repository.release_document_operation(row["submission_id"], token)
                token = None
                if completed:
                    self.documents.workflow_service.advance_after_action(
                        self.repository.get_by_id(row["submission_id"]), definition)
                return {"is_valid": True, "completed": completed, "result": "VALID_SIGNATURE",
                        "message": "Dokument został przyjęty."}
            requires_signature = bool(policy[f"{signer}_signature"])
            validate_pdf_upload(uploaded_file.filename, data, uploaded_file.mimetype)
            mime_type = "application/pdf"
            extension = "pdf"
            digest = sha256(data).hexdigest()
            step_key = sha256(step_id.encode("utf-8")).hexdigest()[:12]
            document_key = sha256(document_id.encode("utf-8")).hexdigest()[:12]
            filename = f"{row['submission_id']}-{step_key}-{document_key}-{signer}-{digest[:16]}-{uuid4().hex[:8]}.{extension}"
            try:
                pdf_write = self.documents.document_storage_service.save_pdf_recoverable(storage=self.documents.storage,
                    slug=row["form_slug"], filename=filename, document_bytes=data,
                    document_type=self.documents._storage_document_type(document_id),
                    signed=requires_signature)
                storage_path = pdf_write.storage_path
            except Exception:
                target["verification_result"] = "VERIFICATION_ERROR"
                target["substate"] = "verification_error"
                self._save(row, step, state, token, "verification_error", "DOCUMENT_STORAGE_FAILED", signer)
                return {"is_valid": False, "completed": False, "result": "VERIFICATION_ERROR",
                        "message": VERIFICATION_MESSAGES["VERIFICATION_ERROR"]}
            verification = {}
            outcome = "VALID_SIGNATURE"
            verification_message = "Dokument został przyjęty."

            if requires_signature:
                signature_outcome = "VERIFICATION_ERROR"
                signature_valid = False

                document_valid = True
                document_status = "VALID"
                document_reason_code = "OK"

                try:
                    verification = dict(
                        current_app.extensions[
                            "services"
                        ].document_signing_service.verify_stored_pdf(
                            data,
                            current_app.config["TEMP_DIR"],
                            storage=self.documents.storage,
                            slug=row["form_slug"],
                            filename=filename,
                            storage_path=storage_path,
                            allowed_signatures=(
                                document.get("allowed_signatures", [])
                                if signer == "participant"
                                else None
                            ),
                        )
                    )

                    signature_outcome = signature_verification_result(
                        verification,
                        (
                            document.get("allowed_signatures", [])
                            if signer == "participant"
                            else None
                        ),
                    )
                    signature_valid = signature_outcome == "VALID_SIGNATURE"

                except Exception:
                    verification = dict(verification or {})
                    verification["reason_code"] = "VERIFIER_EXCEPTION"

                original_name = (
                    office_source
                    if signer == "office"
                    else target.get("generated_filename")
                )

                if original_name:
                    try:
                        original = self.documents.read_document_bytes_for_download(
                            row,
                            original_name,
                            signed=(
                                signer == "office"
                                and policy["participant_signature"]
                            ),
                        )

                        document_valid = data.startswith(original)

                        if not document_valid:
                            document_status = "INVALID"
                            document_reason_code = "DOCUMENT_REVISION_MISMATCH"

                    except Exception:
                        document_valid = False
                        document_status = "ERROR"
                        document_reason_code = "DOCUMENT_READ_ERROR"

                if (
                    signer == "office"
                    and policy["participant_signature"]
                    and int(verification.get("signature_count") or 0) < 2
                ):
                    signature_outcome = "INVALID_SIGNATURE"
                    signature_valid = False
                    verification["reason_code"] = "MISSING_COUNTERSIGNATURE"

                verification["signature_result_code"] = signature_outcome
                verification["document_validation_status"] = document_status
                verification["document_reason_code"] = document_reason_code

                if not signature_valid:
                    outcome = signature_outcome
                    verification["reason_code"] = (
                        verification.get("reason_code")
                        or signature_outcome
                    )

                elif not document_valid:
                    outcome = (
                        "VERIFICATION_ERROR"
                        if document_status == "ERROR"
                        else "INVALID_SIGNATURE"
                    )
                    verification["reason_code"] = document_reason_code

                else:
                    outcome = "VALID_SIGNATURE"
                    verification["reason_code"] = "OK"

                verification_message = _signature_document_message(
                    signature_valid=signature_valid,
                    signature_outcome=signature_outcome,
                    document_status=document_status,
                )
            accepted = outcome == "VALID_SIGNATURE"
            verification["result_code"] = outcome
            recorded = self.documents.submission_document_service.record_document_metadata(
                submission_id=row["submission_id"], form_slug=row["form_slug"], filename=filename,
                file_bytes=data, document_id=document_id,
                document_type=self.documents.workflow_upload_metadata_type(document_id, signer=signer,
                    signed=requires_signature), original_filename=uploaded_file.filename,
                storage_path=storage_path, workflow_step_at_upload=step_id, training_key=instance,
                agreement_number=next((file.get("agreement_number") or "" for file in metadata
                    if file.get("filename") == target.get("generated_filename")), ""), mime_type=mime_type,
                signed=bool(accepted and requires_signature),
                status="signed" if accepted and requires_signature else "uploaded" if accepted else "rejected",
                signature_status=str(verification.get("validation_status") or "not_required").lower(),
                signature_validation_result=verification, uploaded_by_source=signer)
            if not recorded:
                try:
                    self.documents.document_storage_service.rollback_pdf_write(
                        storage=self.documents.storage,
                        receipt=pdf_write,
                    )
                except Exception:
                    current_app.logger.exception(
                        "document_storage_rollback_failed submission_id=%s document_id=%s filename=%s",
                        row["submission_id"],
                        document_id,
                        filename,
                    )
                target["verification_result"] = "VERIFICATION_ERROR"
                target["substate"] = "verification_error"
                self._save(row, step, state, token, "verification_error", "DOCUMENT_METADATA_FAILED", signer)
                return {"is_valid": False, "completed": False, "result": "VERIFICATION_ERROR",
                        "message": VERIFICATION_MESSAGES["VERIFICATION_ERROR"]}
            current_app.logger.info(
                "signature_verification_result submission_id=%s document_id=%s result=%s reason_code=%s "
                "detected_signature_type=%s verifier_backend=%s provider_allowed=%s signature_count=%s "
                "cryptographic_valid=%s document_integrity_valid=%s certificate_chain_valid=%s timestamp_valid=%s",
                row["submission_id"], document_id, outcome,
                verification.get("reason_code") or outcome,
                verification.get("detected_signature_type") or verification.get("signature_type"),
                verification.get("verifier_backend"), verification.get("provider_allowed"),
                verification.get("signature_count"), verification.get("cryptographic_valid"),
                verification.get("document_integrity_valid"), verification.get("certificate_chain_valid"),
                verification.get("timestamp_valid"),
            )
            if not accepted:
                target["verification_result"] = outcome
                target["verification_message"] = verification_message
                target["substate"] = (
                    "verification_error"
                    if outcome == "VERIFICATION_ERROR"
                    else "verification_failed"
                )
                self._save(
                    row,
                    step,
                    state,
                    token,
                    target["substate"],
                    "SIGNATURE_VERIFICATION_FAILED",
                    signer,
                )
                return {
                    "is_valid": False,
                    "completed": False,
                    "result": outcome,
                    "message": verification_message,
                }

            target.pop("verification_result", None)
            target.pop("verification_message", None)
            if signer == "participant" and requires_signature and document_id in {"declaration", "agreement", "training_agreement"}:
                self.documents.record_composite_participant_signature(row, {**step, "document_id": document_id},
                    data, filename, instance, verification)
            if signer == "office":
                self.documents.record_composite_office_signature(row, {**step, "document_id": document_id}, filename, instance)
                target.update(office_filename=filename, completed=True)
                target["substate"] = "completed"
            else:
                target.update(uploaded_filename=filename, completed=not policy["office_signature"])
                target["substate"] = "awaiting_office_signature" if policy["office_signature"] else "completed"
            if collection:
                item["completed"] = bool(item.get("files")) and all(
                    (item.get("instances") or {}).get(file["training_key"], {}).get("completed")
                    for file in item["files"])
            if self._group_satisfied(state, documents, step):
                self._complete(row, step, state, token)
                completed = True
            else:
                self._save(row, step, state, token, "upload", "DOCUMENT_GROUP_FILE_ACCEPTED", signer)
        finally:
            if token:
                self.repository.release_document_operation(row["submission_id"], token)
        if completed:
            self.documents.workflow_service.advance_after_action(self.repository.get_by_id(row["submission_id"]), definition)
        return {
            "is_valid": True,
            "completed": completed,
            "result": "VALID_SIGNATURE",
            "message": verification_message,
        }

    def _download_group(self, row, step, definition, filename, signer="participant", document_id=None):
        documents = step_documents(definition, step)

        for document in documents:
            if document_id and document["id"] != document_id:
                continue
            item = document_item_state(
                row,
                step["id"],
                document["id"],
                primary_document_id=str(step.get("document_id") or ""),
            )
            if self.needs_data(document, item):
                continue
            allowed_names = {item.get("generated_filename"), item.get("uploaded_filename"), item.get("office_filename")}
            for instance in (item.get("instances") or {}).values():
                allowed_names.update({instance.get("generated_filename"), instance.get("uploaded_filename"),
                                      instance.get("office_filename")})
            if filename not in allowed_names:
                continue
            if signer == "participant" and not group_document_policy(step, document)["download"]:
                break
            metadata = self.documents.submission_document_service.list_documents(row["submission_id"])
            file = next((file for file in metadata if file.get("filename") == filename
                         and file.get("document_id") == document["id"]
                         and file.get("workflow_step_at_upload") == step["id"]
                          and file.get("status") not in {"superseded", "rejected", "rejected_no_capacity"}), None)
            if file:
                content = self.documents.document_storage_service.read_document_bytes(
                    storage=self.documents.storage, slug=row["form_slug"], filename=filename,
                    metadata=file, submission_id=row["submission_id"], strict_metadata=True)
                if ((row.get("workflow_step") or row.get("workflow_stage")) == step["id"]
                        and signer == "participant"
                        and group_document_policy(step, document)["upload_required"]
                        and item.get("substate") == "ready"):
                    token = self._claim(row, step)
                    try:
                        fresh = document_step_state(self.repository.get_by_id(row["submission_id"]), step["id"])
                        current_item = ensure_document_item_state(fresh, document["id"],
                            primary_document_id=str(step.get("document_id") or ""))
                        current_item["substate"] = "awaiting_signature"
                        self._save(row, step, fresh, token, "awaiting_signature", "DOCUMENT_DOWNLOADED", "participant")
                    finally:
                        self.repository.release_document_operation(row["submission_id"], token)
                return content
        raise ValueError("Dokument nie jest dostępny do pobrania.")

    def _view_group(self, row, step, definition, signer="participant"):
        documents = step_documents(definition, step)
        state = document_step_state(row, step["id"])

        required_count = sum(
            bool(document.get("required", True))
            for document in documents
        )

        ready_count = sum(
            bool(document.get("required", True))
            and bool(
                document_item_state(
                    row,
                    step["id"],
                    document["id"],
                    primary_document_id=str(step.get("document_id") or ""),
                ).get("completed")
            )
            for document in documents
        )
        files = []
        generation_actions = []
        form_definition = None
        form_document_id = ""
        form_values = None
        waiting_for_collection_selection = False
        for document in documents:
            item = document_item_state(
                row,
                step["id"],
                document["id"],
                primary_document_id=str(step.get("document_id") or ""),
            )

            policy = group_document_policy(step, document)
            needs_data = (
                signer == "participant"
                and self.needs_data(document, item)
            )

            if needs_data and form_definition is None:
                operational_document = current_app.extensions[
                    "services"
                ].declaration_flow_service.operational_document(
                    document,
                    row,
                    self.repository,
                )

                form_definition = current_app.extensions[
                    "services"
                ].declaration_flow_service.document_fields_definition(
                    operational_document
                )

                form_document_id = document["id"]
                form_values = dict(row)
            if self._is_collection_document(document):
                collection_field = document.get("repeat_over") or "selected_trainings"
                if (
                    not parse_training_snapshots(row.get(collection_field))
                    and not item.get("files")
                    and not item.get("generated_filename")
                    and not item.get("uploaded_filename")
                    and not item.get("office_filename")
                ):
                    waiting_for_collection_selection = True
                    collection_field = document.get("repeat_over") or "selected_trainings"
                    selected_collection = parse_training_snapshots(row.get(collection_field))

                    if (
                        signer == "participant"
                        and policy["generate"]
                        and selected_collection
                        and not item.get("completed")
                        and not any(
                            (instance or {}).get("generated_filename")
                            for instance in (item.get("instances") or {}).values()
                        )
                    ):
                        generation_actions.append({
                            "document_id": document["id"],
                            "label": participant_document_label(document),
                        })
                for source in item.get("files") or []:
                    key = source.get("training_key") or ""
                    target = (item.get("instances") or {}).get(key) or {}
                    filename = (target.get("office_filename") or target.get("uploaded_filename") or
                                target.get("generated_filename") or "")
                    office_source = target.get("uploaded_filename") or (
                        target.get("generated_filename") if not policy["upload_required"] else "")
                    can_upload = (bool(policy["office_signature"] and office_source and not target.get("office_filename"))
                                  if signer == "office" else bool(policy["upload_required"] and
                                      (not policy["generate"] or target.get("generated_filename")) and
                                      not target.get("completed") and not target.get("uploaded_filename")))
                    files.append({"document_id": document["id"], "instance": key,
                                  "label": self._public_file_label(row, document, source),
                                  "description": document.get("description") or "",
                                  "filename": filename if policy["download"] or signer == "office" else "",
                                  "required": bool(document.get("required", True)),
                                  "signature_required": policy["participant_signature"],
                                  "office_signature_required": policy["office_signature"],
                                 "can_generate": bool(
                                    policy["generate"]
                                    and not needs_data
                                    and bool(parse_training_snapshots(
                                        row.get(document.get("repeat_over") or "selected_trainings")
                                    ))
                                    and not target.get("generated_filename")
                                    and not target.get("completed")
                                   ),
                                  "can_upload": can_upload,
                                  "verification_result": target.get("verification_result") or "",
                                "verification_message": (
                                    target.get("verification_message")
                                    or VERIFICATION_MESSAGES.get(
                                        target.get("verification_result"),
                                        "",
                                    )
                                ),
                                  "signed": bool(target.get("office_filename")),
                                  "completed": bool(target.get("completed")),
                                  "status": "Gotowy" if target.get("completed") else
                                      "Oczekuje na podpis urzędu" if target.get("uploaded_filename") and policy["office_signature"] else
                                      "Oczekuje na podpis" if filename else "Brak dokumentu",
                                  "accept": ".pdf,application/pdf"})
                continue
            filename = (item.get("uploaded_filename") or item.get("generated_filename") or "") if signer == "office" else (
                item.get("office_filename") or item.get("uploaded_filename") or item.get("generated_filename") or "")
            office_source = item.get("uploaded_filename") or (
                item.get("generated_filename") if not policy["upload_required"] else "")
            can_upload = (bool(policy["office_signature"] and office_source and not item.get("office_filename"))
                          if signer == "office" else bool(policy["upload_required"] and
                              (not policy["generate"] or item.get("generated_filename")) and
                              not item.get("completed") and not item.get("uploaded_filename")))
            if item.get("substate") == "failed" and not filename:
                continue
            files.append({"document_id": document["id"], "label": participant_document_label(document),
                          "description": document.get("description") or "", "filename": filename if policy["download"] or signer == "office" else "",
                          "required": bool(document.get("required", True)), "signature_required": policy["participant_signature"],
                          "office_signature_required": policy["office_signature"],
                          "can_generate": bool(
                                policy["generate"]
                                and not needs_data
                                and not item.get("generated_filename")
                                and not item.get("completed")
                            ),
                        "verification_result": item.get("verification_result") or "",
                        "verification_message": (
                            item.get("verification_message")
                            or VERIFICATION_MESSAGES.get(
                                item.get("verification_result"),
                                "",
                            )
                        ),
                          "signed": bool(item.get("office_filename")), "instance": "",
                          "completed": bool(item.get("completed")), "can_upload": can_upload,
                          "status": (
                                "Uzupełnij dane"
                                if needs_data
                                else "Gotowy"
                                if item.get("completed")
                                else "Oczekuje na podpis urzędu"
                                if item.get("uploaded_filename") and policy["office_signature"]
                                else "Oczekuje na podpis"
                                if filename and policy["participant_signature"]
                                else "Brak dokumentu"
                            ),
                          "accept": ".pdf,application/pdf"})
        instruction = step.get("user_instruction") or step.get("description") or (
            f"Na tym etapie wymaganych jest {required_count} dokumentów. "
            f"{sum(bool(group_document_policy(step, document)['participant_signature']) for document in documents)} wymaga podpisu elektronicznego.")
        can_generate = any(file["can_generate"] for file in files)
        aggregate_status = document_step_status(row, step)
        aggregate_substate = (
            "collect_data"
            if form_definition is not None
            else aggregate_status["substate"]
        )
        if (
            aggregate_substate == "failed"
            and waiting_for_collection_selection
        ):
            aggregate_substate = "ready"
        return {"step_id": step["id"], "label": step.get("admin_label") or "Dokumenty", "group": True,
                "files": files, "generation_actions": generation_actions, "required_count": required_count, "ready_count": ready_count,
                "status": {"title": step.get("label") or "Dokumenty", "message": instruction},
                "instruction": (step.get("document_instructions") or {}).get(aggregate_substate) or {},
                "substate": aggregate_substate, "policy": {}, "can_generate": can_generate,
                "can_retry": can_generate or aggregate_substate in {"failed", "verification_error", "verification_failed"}, "form_definition": form_definition,
                "form_document_id": form_document_id, "values": form_values or dict(row),}

    def version_gap_files(self, submission, definition, document_id):
        item = self.documents._version_gap_entry(submission, definition, document_id)
        row = self.repository.get_by_id(self.documents._submission_id(submission))
        files = self.documents.submission_document_service.list_documents(row["submission_id"])
        return [file for file in files if file.get("document_id") == document_id
                and file.get("workflow_step_at_upload") == item["step_id"]
                and file.get("status") not in {"superseded", "rejected", "rejected_no_capacity"}]

    def generate_version_gap(self, submission, definition, document_id):
        document = self.documents.get_document_by_id(definition, document_id)
        if not document or document.get("kind") != "generated_pdf":
            raise ValueError("Ten dokument nie jest generowany przez system.")
        existing = next((file for file in self.version_gap_files(submission, definition, document_id)
                         if file.get("status") == "generated"), None)
        if existing:
            return existing
        result = self.documents.generate_document(submission, definition, document_id, version_gap=True)
        self._refresh_version_gap(submission)
        return result

    def upload_version_gap(self, submission, definition, document_id, uploaded_file):
        if not uploaded_file or not uploaded_file.filename:
            raise ValueError("Wybierz plik dokumentu.")
        marker = self.documents._version_gap_entry(submission, definition, document_id)
        document = self.documents.get_document_by_id(definition, document_id)
        row = self.repository.get_by_id(self.documents._submission_id(submission))
        files = self.version_gap_files(row, definition, document_id)
        if any(file.get("status") in {"signed", "uploaded"} for file in files):
            raise ValueError("Dokument został już przyjęty.")
        signature_required = bool(document.get("signature_required", True))
        if document.get("kind") == "generated_pdf" and not signature_required:
            raise ValueError("Ten dokument kończy się po wygenerowaniu.")
        data = uploaded_file.read()
        validate_pdf_upload(uploaded_file.filename, data, uploaded_file.mimetype)
        mime_type, extension = "application/pdf", "pdf"
        generated = next((file for file in files if file.get("status") == "generated"), None)
        if document.get("kind") == "generated_pdf" and not generated:
            raise ValueError("Najpierw wygeneruj dokument.")
        digest = sha256(data).hexdigest()
        filename = f"{row['submission_id']}-{sha256(document_id.encode()).hexdigest()[:12]}-upgrade-{digest[:16]}.{extension}"
        pdf_write = self.documents.document_storage_service.save_pdf_recoverable(storage=self.documents.storage,
            slug=row["form_slug"], filename=filename, document_bytes=data,
            document_type=self.documents._storage_document_type(document_id), signed=signature_required)
        storage_path = pdf_write.storage_path
        verification = {}
        outcome = "VALID_SIGNATURE"
        verification_message = "Dokument został przyjęty."

        if signature_required:
            signature_outcome = "VERIFICATION_ERROR"
            signature_valid = False

            document_valid = True
            document_status = "VALID"
            document_reason_code = "OK"

            try:
                verification = dict(
                    current_app.extensions[
                        "services"
                    ].document_signing_service.verify_stored_pdf(
                        data,
                        current_app.config["TEMP_DIR"],
                        storage=self.documents.storage,
                        slug=row["form_slug"],
                        filename=filename,
                        storage_path=storage_path,
                        allowed_signatures=document.get(
                            "allowed_signatures",
                            [],
                        ),
                    )
                )

                signature_outcome = signature_verification_result(
                    verification,
                    document.get("allowed_signatures", []),
                )
                signature_valid = signature_outcome == "VALID_SIGNATURE"

            except Exception:
                verification = dict(verification or {})
                verification["reason_code"] = "VERIFIER_EXCEPTION"

            if generated:
                try:
                    original = self.documents.read_document_bytes_for_download(
                        row,
                        generated["filename"],
                        signed=False,
                    )

                    document_valid = data.startswith(original)

                    if not document_valid:
                        document_status = "INVALID"
                        document_reason_code = "DOCUMENT_REVISION_MISMATCH"

                except Exception:
                    document_valid = False
                    document_status = "ERROR"
                    document_reason_code = "DOCUMENT_READ_ERROR"

            verification["signature_result_code"] = signature_outcome
            verification["document_validation_status"] = document_status
            verification["document_reason_code"] = document_reason_code

            if not signature_valid:
                outcome = signature_outcome
                verification["reason_code"] = (
                    verification.get("reason_code")
                    or signature_outcome
                )

            elif not document_valid:
                outcome = (
                    "VERIFICATION_ERROR"
                    if document_status == "ERROR"
                    else "INVALID_SIGNATURE"
                )
                verification["reason_code"] = document_reason_code

            else:
                outcome = "VALID_SIGNATURE"
                verification["reason_code"] = "OK"

            verification_message = _signature_document_message(
                signature_valid=signature_valid,
                signature_outcome=signature_outcome,
                document_status=document_status,
            )
        accepted = outcome == "VALID_SIGNATURE"
        verification["result_code"] = outcome
        recorded = self.documents.submission_document_service.record_document_metadata(
            submission_id=row["submission_id"], form_slug=row["form_slug"], filename=filename, file_bytes=data,
            document_id=document_id, document_type=self.documents.workflow_upload_metadata_type(document_id,
                signer="participant", signed=signature_required), original_filename=uploaded_file.filename,
            storage_path=storage_path, workflow_step_at_upload=marker["step_id"], mime_type=mime_type,
            signed=bool(signature_required and accepted),
            status="signed" if signature_required and accepted else "uploaded" if accepted else "rejected",
            signature_status=str(verification.get("validation_status") or "not_required").lower(),
            signature_validation_result=verification, uploaded_by_source="participant")
        if not recorded:
            try:
                self.documents.document_storage_service.rollback_pdf_write(
                    storage=self.documents.storage,
                    receipt=pdf_write,
                )
            except Exception:
                current_app.logger.exception(
                    "document_storage_rollback_failed submission_id=%s document_id=%s filename=%s",
                    row["submission_id"],
                    document_id,
                    filename,
                )
            raise ValueError("Nie udało się zapisać metadanych dokumentu.")
        if accepted:
            self._refresh_version_gap(submission)
        return {
            "is_valid": accepted,
            "result": outcome,
            "message": verification_message,
        }

    def _refresh_version_gap(self, submission):
        from sqlalchemy import select
        from models import FormSubmission
        from services.form_version_service import FormVersionService
        with self.repository.session_factory() as db:
            model = db.execute(select(FormSubmission).where(FormSubmission.submission_id ==
                self.documents._submission_id(submission)).with_for_update()).scalar_one()
            FormVersionService().refresh_submission_completion(db, model)
            db.commit()

    def download_version_gap(self, submission, definition, document_id, filename):
        row = self.repository.get_by_id(self.documents._submission_id(submission))
        file = next((file for file in self.version_gap_files(row, definition, document_id)
                     if file.get("filename") == filename and file.get("status") == "generated"), None)
        if not file:
            raise ValueError("Dokument nie jest dostępny.")
        return self.documents.document_storage_service.read_document_bytes(
            storage=self.documents.storage, slug=row["form_slug"], filename=filename,
            metadata=file, submission_id=row["submission_id"], strict_metadata=True)

    def start(self, submission, definition):
        row, step, _ = self._current(
            submission,
            definition,
        )

        return self._start_group(
            row,
            step,
            definition,
        )

    def _complete(self, row, step, state, token):
        if not state.get("completed"):
            state["completed"] = True
            self._save(row, step, state, token, "completed", "DOCUMENT_STEP_COMPLETED")

    def download(
        self,
        submission,
        definition,
        step_id,
        filename,
        *,
        signer="participant",
        document_id=None,
    ):
        # Dokumenty z zakończonych etapów pozostają dostępne dla uczestnika.
        row = self.repository.get_by_id(
            self.documents._submission_id(submission)
        )

        step = next(
            (
                item
                for item in definition.get("workflow", {}).get("steps", [])
                if item.get("id") == step_id
                and has_document_workflow(definition, item)
            ),
            None,
        )

        if not step:
            raise ValueError("Nie znaleziono dokumentu.")

        return self._download_group(
            row,
            step,
            definition,
            filename,
            signer,
            document_id,
        )
    def upload(
        self,
        submission,
        definition,
        step_id,
        uploaded_file,
        *,
        signer="participant",
        instance="",
        document_id="",
    ):
        return self._upload_group(
            submission,
            definition,
            step_id,
            uploaded_file,
            document_id,
            signer,
            instance,
        )

    def view(self, row, definition, *, signer="participant"):
        step = self.documents.composite_step(row, definition)

        if not step:
            return None

        return self._view_group(
            row,
            step,
            definition,
            signer,
        )

    def completed_files(self, row, definition):
        files = []

        for step in definition.get("workflow", {}).get("steps", []):
            documents = step_documents(definition, step)

            if not documents:
                continue

            state = document_step_state(row, step.get("id"))

            if not state.get("completed"):
                continue

            for document in documents:
                item = document_item_state(
                    row,
                    step["id"],
                    document["id"],
                    primary_document_id=str(step.get("document_id") or ""),
                )

                if self._is_collection_document(document):
                    for source in item.get("files") or []:
                        key = source.get("training_key") or ""

                        instance = (
                            (item.get("instances") or {}).get(key)
                            or {}
                        )

                        filename = (
                            instance.get("office_filename")
                            or instance.get("uploaded_filename")
                            or instance.get("generated_filename")
                        )

                        if (
                            filename
                            and group_document_policy(
                                step,
                                document,
                            )["download"]
                        ):
                            files.append({
                                "step_id": step["id"],
                                "document_id": document["id"],
                                "label": self._public_file_label(
                                    row,
                                    document,
                                    source,
                                ),
                                "filename": filename,
                            })

                    continue

                filename = (
                    item.get("office_filename")
                    or item.get("uploaded_filename")
                    or item.get("generated_filename")
                )

                if (
                    filename
                    and group_document_policy(
                        step,
                        document,
                    )["download"]
                ):
                    files.append({
                        "step_id": step["id"],
                        "document_id": document["id"],
                        "label": participant_document_label(document),
                        "filename": filename,
                    })

        return files
