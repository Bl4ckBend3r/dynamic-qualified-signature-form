"""Composite document actions owned by DocumentService, using its files and renderer."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from uuid import uuid4
import json
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


def is_document_step(step):
    return step.get("stage_type", step.get("type")) == "document" and step.get("document_lifecycle") == "composite"


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
        "upload_required": policy.get("upload_required", participant),
    }


def document_states(row):
    states = row.get("document_states") or {}
    if isinstance(states, str):
        states = json.loads(states)
    return states if isinstance(states, dict) else {}


def document_step_state(row, step_id):
    return deepcopy((document_states(row).get("workflow_documents") or {}).get(step_id) or {})


def requirements_satisfied(state, policy):
    roles = (["participant"] if policy["upload_required"] else []) + (["office"] if policy["office_signature"] else [])
    return bool(state.get("files") and roles and all(all(state.get("signatures", {}).get(f.get("training_key") or "document", {}).get(role) for role in roles) for f in state["files"]))


def document_step_status(row, step):
    state = document_step_state(row, step["id"])
    substate = state.get("substate", "generating")
    label = step.get("user_label") or step.get("admin_label") or "Dokument"
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
    message = messages.get(substate, "Przygotowujemy dokument.")
    if substate in {"verification_failed", "verification_error"}:
        message = VERIFICATION_MESSAGES.get(state.get("verification_result"), message)
    return {"title": f"{label} — {DOCUMENT_SUBSTATES.get(substate, 'Przygotowanie dokumentu').lower()}",
            "message": message, "substate": substate,
            "variant": "danger" if substate in {"failed", "verification_failed", "verification_error"} else "info"}


def validate_document_step(step, form_config):
    from services.form_config_service import FormConfigService
    documents = FormConfigService().normalize_documents_config(form_config)
    document = next((d for d in documents if d.get("id") == step.get("document_id") and d.get("enabled", True)), None)
    if not document:
        return ["Etap dokumentowy musi wskazywać istniejący, włączony dokument."]
    errors = []
    if not step.get("next") or step.get("final") or step.get("transitions"):
        errors.append("Złożony etap dokumentowy wymaga jednego kolejnego etapu.")
    if not isinstance(step.get("document_options", {}), dict):
        return [*errors, "Przebieg dokumentu musi być obiektem konfiguracji."]
    policy = document_policy(step, document)
    if any(not isinstance(value, bool) for value in policy.values()):
        errors.append("Ustawienia przebiegu dokumentu muszą być wartościami logicznymi.")
    if policy["participant_signature"] and not policy["upload_required"]:
        errors.append("Podpis uczestnika wymaga możliwości wgrania dokumentu.")
    if not policy["generate"] and not policy["upload_required"] and not policy["office_signature"]:
        errors.append("Dokument musi być generowany lub przyjmowany przez upload.")
    if policy["generate"] and not any(document.get(k) for k in ("template", "template_html", "builder_document", "template_metadata")):
        errors.append("Generowany dokument wymaga skonfigurowanego szablonu.")
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
                pass
        for item in parse_training_snapshots(row.get("selected_trainings")):
            if str(item.get("id") or "") == training_key:
                name = str(item.get("name") or "").strip()
                if name:
                    return name
        return str(source.get("training_name") or source.get("label") or "").strip()

    def _public_file_label(self, row, document, source):
        if str(document.get("id") or "") not in {"agreement", "training_agreement"}:
            return str(source.get("label") or document.get("label") or "Dokument")
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
            collection = self.documents.get_document_by_id(definition, step.get("document_id")) or {}
            if collection.get("repeat_over") != "selected_trainings" and collection.get("generation_mode") != "per_training":
                continue
            state = (states.get("workflow_documents") or {}).get(step["id"])
            if not state or not state.get("files"):
                continue
            files = {file.get("training_key"): file for file in state["files"]}
            if set(files) == set(selected):
                continue
            state["files"] = [files.get(key) or {"filename": "", "training_key": key, "label": item["name"]}
                              for key, item in selected.items()]
            state["completed"] = False
            state["substate"] = "generating"
            state["status"] = DocumentState.PENDING.value
        submission.document_states = states
        document = self.documents.get_document_by_id(definition, "declaration")
        if not document:
            return
        declaration_states = [state for state in (states.get("workflow_documents") or {}).values()
                              if state.get("document_id") == "declaration"]
        if not (str(submission.declaration_generated or "").lower() == "tak"
                or (submission.data_json or {}).get("_signed_declaration_snapshot")
                or any(state.get("files") or state.get("data_confirmed") for state in declaration_states)):
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
        steps = [s for s in definition.get("workflow", {}).get("steps", [])
                 if is_document_step(s) and s.get("document_id") == "declaration"]
        data = dict(submission.data_json or {})
        snapshot = data.pop("_signed_declaration_snapshot", None)
        now = datetime.now(timezone.utc)
        if snapshot:
            data["_declaration_signature_history"] = [*data.get("_declaration_signature_history", []),
                {**snapshot, "invalidated_at": now.isoformat(), "reason": "training_selection_changed"}]
            submission.data_json = data
        affected = False
        for step in steps:
            state = (states.get("workflow_documents") or {}).get(step["id"], {})
            if not state.get("files") and not state.get("data_confirmed"):
                continue
            affected = True
            states["workflow_documents"][step["id"]] = {
                "document_id": "declaration", "substate": "collect_data",
                "status": DocumentState.PENDING.value, "data_confirmed": False, "regenerate": True}
            submission.workflow_stage = submission.workflow_step = step["id"]
            submission.process_status = step["status"]
            submission.final_outcome = ""
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

    def _current(self, submission, definition, expected_step=None):
        row = self.repository.get_by_id(self.documents._submission_id(submission))
        if not row:
            raise ValueError("Nie znaleziono zgłoszenia.")
        current = row.get("workflow_stage") or row.get("workflow_step")
        step = next((s for s in definition.get("workflow", {}).get("steps", []) if s.get("id") == current), {})
        if not is_document_step(step) or (expected_step and current != expected_step):
            raise ValueError("Etap dokumentowy nie jest już aktywny.")
        document = self.documents.get_document_by_id(definition, step.get("document_id"))
        if not document or not document.get("enabled", True):
            raise ValueError("Brak skonfigurowanego dokumentu.")
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
        state["document_id"] = step["document_id"]
        if not self.repository.save_document_step(row["submission_id"], step["id"], state, token, event, actor, field_values=field_values):
            raise ValueError("Etap zmienił się podczas obsługi dokumentu.")

    def _files(self, row, step):
        return [f for f in self.documents.submission_document_service.list_documents(row["submission_id"])
                if f.get("document_id") == step["document_id"] and f.get("workflow_step_at_upload") == step["id"]
                and f.get("status") not in {"superseded", "rejected", "rejected_no_capacity"}]

    @staticmethod
    def needs_data(document, state):
        return (any(f.get("name") and f.get("type") not in {"section", "static_text"} for f in document.get("fields") or [])
                and not state.get("data_confirmed") and not state.get("completed")
                and not any(state.get("signatures", {}).values()))

    def save_fields(self, submission, definition, step_id, form_data, *, generate=True):
        row, step, document = self._current(submission, definition, step_id)
        token = self._claim(row, step)
        try:
            row = self.repository.get_by_id(row["submission_id"])
            state = document_step_state(row, step_id)
            if (state.get("data_confirmed") and state.get("files")) or state.get("completed") or any(state.get("signatures", {}).values()):
                raise ValueError("Dane wygenerowanego dokumentu są zablokowane. Zmiana wymaga korekty dokumentu.")
            flow, data = current_app.extensions["services"].declaration_flow_service.validate_document_fields(
                document, row, form_data, self.repository)
            if not flow.success:
                return flow
            current_app.extensions["services"].declaration_flow_service.save_document_training_selection(
                document, row, form_data, current_app.extensions["services"])
            saved_row = self.repository.get_by_id(row["submission_id"])
            for field in document.get("fields") or []:
                if field.get("type") == "training_selection":
                    data[field["name"]] = saved_row.get("selected_trainings") or "[]"
            state["data_confirmed"] = generate
            if generate:
                state["data_sha256"] = sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                # Old, prematurely generated PDFs must never be reused after collecting data.
                for file in self._files(row, step):
                    if not file.get("signed"):
                        if not self.repository.record_file(row["submission_id"], {**file, "status": "superseded"}):
                            raise RuntimeError("Nie udało się zastąpić poprzedniej wersji dokumentu.")
                state["files"] = []
                state["regenerate"] = True
            self._save(row, step, state, token, "collect_data", "DOCUMENT_FIELDS_SAVED", "participant", field_values=data)
        finally:
            self.repository.release_document_operation(row["submission_id"], token)
        if generate:
            self.documents.workflow_service.run_automatic_steps(self.repository.get_by_id(row["submission_id"]), definition)
        return flow

    def start(self, submission, definition):
        row, step, document = self._current(submission, definition)
        token = self._claim(row, step)
        context_token = self.documents._document_operation_token.set(token)
        try:
            row.update(self.repository.get_by_id(row["submission_id"]))
            state = document_step_state(self.repository.get_by_id(row["submission_id"]), step["id"])
            if state.get("completed"):
                return True
            if self.needs_data(document, state):
                if state.get("substate") != "collect_data":
                    self._save(row, step, state, token, "collect_data", "DOCUMENT_DATA_REQUESTED")
                return False
            policy = document_policy(step, document)
            state["signature_required"] = bool(policy["participant_signature"] or policy["office_signature"])
            if requirements_satisfied(state, policy):
                self._complete(row, step, state, token)
                return True
            if state.get("substate") not in {None, "collect_data", "generating", "failed"}:
                return False
            if policy["generate"]:
                self._save(row, step, state, token, "generating", "DOCUMENT_GENERATION_STARTED")
                generated = [f for f in self._files(row, step) if not f.get("signed")] if not state.get("regenerate") else []
                collection = bool(document.get("repeat_over") or document.get("generation_mode") == "per_training")
                if collection:
                    generated = self.documents.generate_documents_for_collection(row, definition, step["document_id"], document.get("repeat_over") or "selected_trainings", document.get("repeat_item_alias") or "training")
                elif not generated:
                    generated = [self.documents.generate_document(row, definition, step["document_id"])]
                if not generated or any(not f.get("filename") for f in generated):
                    raise ValueError("Nie udało się wygenerować wszystkich dokumentów.")
                state["files"] = [{"filename": f["filename"], "training_key": str(f.get("training_key") or (f.get("id") if collection else "") or ""), "training_name": str(f.get("training_name") or ""), "label": f.get("training_name") or document["label"]} for f in generated]
                state.pop("regenerate", None)
                self._save(row, step, state, token, "ready", "DOCUMENT_GENERATED")
            else:
                state["files"] = [{"filename": "", "training_key": ""}]
                self._save(row, step, state, token, "upload" if policy["upload_required"] else "awaiting_office_signature", "DOCUMENT_UPLOAD_REQUESTED")
            if not policy["participant_signature"] and not policy["upload_required"]:
                if policy["office_signature"]:
                    self._save(row, step, state, token, "awaiting_office_signature", "OFFICE_SIGNATURE_REQUESTED")
                else:
                    self._complete(row, step, state, token)
            return bool(state.get("completed"))
        except Exception:
            # Leave failed generation retryable; successfully saved files are reused.
            if 'state' in locals() and state.get("substate") == "generating":
                self._save(row, step, state, token, "failed", "DOCUMENT_GENERATION_FAILED")
            raise
        finally:
            self.documents._document_operation_token.reset(context_token)
            self.repository.release_document_operation(row["submission_id"], token)

    def _complete(self, row, step, state, token):
        if not state.get("completed"):
            state["completed"] = True
            self._save(row, step, state, token, "completed", "DOCUMENT_STEP_COMPLETED")

    def download(self, submission, definition, step_id, filename, *, signer="participant"):
        # Downloads from completed steps remain available to the same participant.
        row = self.repository.get_by_id(self.documents._submission_id(submission))
        step = next((s for s in definition.get("workflow", {}).get("steps", []) if s.get("id") == step_id and is_document_step(s)), None)
        if not step:
            raise ValueError("Nie znaleziono dokumentu.")
        document = self.documents.get_document_by_id(definition, step["document_id"])
        if self.needs_data(document, document_step_state(row, step_id)):
            raise ValueError("Najpierw uzupełnij dane dokumentu.")
        if signer == "participant" and not document_policy(step, document)["download"] or not any(f["filename"] == filename for f in self._files(row, step)):
            raise ValueError("Dokument nie jest dostępny do pobrania.")
        file = next(f for f in self._files(row, step) if f["filename"] == filename)
        data = self.documents.read_document_bytes_for_download(row, filename, signed=bool(file.get("signed")))
        state = document_step_state(row, step_id)
        if (row.get("workflow_stage") or row.get("workflow_step")) == step_id:
            token = self._claim(row, step)
            try:
                state = document_step_state(self.repository.get_by_id(row["submission_id"]), step_id)
                next_state = "awaiting_signature" if signer == "participant" and state.get("substate") == "ready" and document_policy(step, document)["participant_signature"] else state["substate"]
                self._save(row, step, state, token, next_state, "DOCUMENT_DOWNLOADED", signer)
            finally:
                self.repository.release_document_operation(row["submission_id"], token)
        else:
            self.documents.workflow_service._record_workflow_event(row["submission_id"],
                previous_step=step_id, new_step=step_id, previous_status=row.get("process_status"), new_status=row.get("process_status"),
                actor=signer, reason="Pobranie dokumentu", decision_code="", user_message="",
                side_effects={"document_id": step["document_id"], "filename": filename}, source="DOCUMENT_DOWNLOADED")
        return data

    def upload(self, submission, definition, step_id, uploaded_file, *, signer="participant", instance=""):
        if signer not in {"participant", "office"} or not uploaded_file or not uploaded_file.filename:
            raise ValueError("Wybierz plik PDF.")
        current = self.repository.get_by_id(self.documents._submission_id(submission))
        prior = document_step_state(current, step_id)
        if prior.get("completed"):
            previous_filename = prior.get("signatures", {}).get(instance or "document", {}).get(signer)
            metadata = self.documents.submission_document_service.list_documents(current["submission_id"])
            digest = sha256(uploaded_file.read()).hexdigest()
            if any(f["filename"] == previous_filename and f.get("checksum_sha256") == digest for f in metadata):
                return {"is_valid": True, "completed": True, "result": "VALID_SIGNATURE", "message": VERIFICATION_MESSAGES["VALID_SIGNATURE"]}
            raise ValueError("Etap dokumentowy został już zakończony.")
        row, step, document = self._current(submission, definition, step_id)
        previous = document_step_state(row, step_id)
        if previous.get("completed") or requirements_satisfied(previous, document_policy(step, document)):
            self.documents.workflow_service.run_automatic_steps(row, definition)
            return {"is_valid": True, "completed": True}
        token = self._claim(row, step)
        completed = False
        try:
            state = document_step_state(self.repository.get_by_id(row["submission_id"]), step_id)
            policy = document_policy(step, document)
            if state.get("completed"):
                return {"is_valid": True, "completed": True}
            if signer == "office" and not policy["office_signature"] or signer == "participant" and not policy["upload_required"]:
                raise ValueError("Ten podpis lub upload nie jest wymagany.")
            files = state.get("files") or []
            source = next((f for f in files if f.get("training_key", "") == instance), None)
            if not source or self.needs_data(document, state) or state.get("substate") in {"collect_data", "generating", "failed"}:
                raise ValueError("Najpierw przygotuj dokument.")
            key = instance or "document"
            signatures = state.setdefault("signatures", {}).setdefault(key, {})
            if signer == "office" and policy["upload_required"] and not signatures.get("participant"):
                raise ValueError("Dokument wymaga najpierw podpisu uczestnika.")
            if signatures.get(signer):
                return {"is_valid": True, "completed": False}
            data = uploaded_file.read()
            validate_pdf_upload(uploaded_file.filename, data, uploaded_file.mimetype)
            requires_signature = policy[f"{signer}_signature"]
            instance_name = sha256(instance.encode()).hexdigest()[:12] if instance else "document"
            filename = f"{row['submission_id']}-{step_id}-{instance_name}-{signer}-{sha256(data).hexdigest()[:16]}.pdf"
            try:
                storage_path = self.documents.document_storage_service.save_pdf(storage=self.documents.storage, slug=row["form_slug"], filename=filename,
                    document_bytes=data, document_type=self.documents._storage_document_type(step["document_id"]), signed=requires_signature)
            except Exception:
                state["verification_result"] = "VERIFICATION_ERROR"
                self._save(row, step, state, token, "verification_error", "DOCUMENT_STORAGE_FAILED", signer)
                self._log_verification(row, step, None, {"upload_sha256": sha256(data).hexdigest()}, "VERIFICATION_ERROR", "STORAGE_WRITE_ERROR")
                return {"is_valid": False, "completed": False, "result": "VERIFICATION_ERROR", "message": VERIFICATION_MESSAGES["VERIFICATION_ERROR"]}
            # Persist the uploaded artifact even when verification fails; it is never accepted as signed.
            metadata = dict(submission_id=row["submission_id"], form_slug=row["form_slug"], filename=filename, file_bytes=data,
                document_id=step["document_id"], document_type=self.documents.workflow_upload_metadata_type(step["document_id"], signer=signer, signed=requires_signature),
                agreement_number=next((f.get("agreement_number", "") for f in self._files(row, step) if f["filename"] == source.get("filename")), ""),
                original_filename=uploaded_file.filename, storage_path=storage_path, training_key=instance, workflow_step_at_upload=step_id)
            pending_metadata = {**metadata, "document_type": f"uploaded_{step['document_id']}_{signer}"}
            self._save(row, step, state, token, "upload", "SIGNED_DOCUMENT_UPLOADED", signer)
            verification = {}
            outcome = "VALID_SIGNATURE"
            if requires_signature:
                self._save(row, step, state, token, "verifying", "SIGNATURE_VERIFICATION_STARTED", signer)
                try:
                    verification = dict(current_app.extensions["services"].document_signing_service.verify_stored_pdf(
                        data, current_app.config["TEMP_DIR"], storage=self.documents.storage, slug=row["form_slug"],
                        filename=filename, storage_path=storage_path,
                        allowed_signatures=document.get("allowed_signatures", []) if signer == "participant" else None))
                    outcome = signature_verification_result(verification, document.get("allowed_signatures", []) if signer == "participant" else None)
                    if outcome == "VALID_SIGNATURE":
                        original_filename = (signatures.get("participant") or source.get("filename")) if signer == "office" else source.get("filename")
                        if original_filename:
                            try:
                                original = self.documents.read_document_bytes_for_download(row, original_filename,
                                    signed=bool(signer == "office" and signatures.get("participant") and policy["participant_signature"]))
                            except Exception:
                                verification["reason_code"] = "STORAGE_READ_ERROR"
                                raise
                            if not data.startswith(original) or data == original:
                                outcome = "INVALID_SIGNATURE"
                                verification["reason_code"] = "DOCUMENT_REVISION_MISMATCH"
                        if signer == "office" and policy["participant_signature"] and int(verification.get("signature_count") or 0) < 2:
                            outcome = "INVALID_SIGNATURE"
                            verification["reason_code"] = "MISSING_COUNTERSIGNATURE"
                except Exception:
                    verification.update(validation_status="ERROR", reason_code=verification.get("reason_code") if verification.get("reason_code") == "STORAGE_READ_ERROR" else "VERIFIER_EXCEPTION")
                    outcome = "VERIFICATION_ERROR"
            valid = outcome == "VALID_SIGNATURE"
            verification["result_code"] = outcome
            recorded = self.documents.submission_document_service.record_document_metadata(**(metadata if valid else pending_metadata), signed=requires_signature,
                status="signed" if valid and requires_signature else "uploaded" if valid else "rejected",
                signature_status=str(verification.get("validation_status") or "not_required").lower(), signature_validation_result=verification)
            if not recorded:
                state["verification_result"] = "VERIFICATION_ERROR"
                self._save(row, step, state, token, "verification_error", "DOCUMENT_METADATA_FAILED", signer)
                self._log_verification(row, step, None, verification, "VERIFICATION_ERROR", "METADATA_WRITE_ERROR")
                return {"is_valid": False, "completed": False, "result": "VERIFICATION_ERROR", "message": VERIFICATION_MESSAGES["VERIFICATION_ERROR"]}
            file = next((f for f in self.documents.submission_document_service.list_documents(row["submission_id"]) if f["filename"] == filename), {})
            self._log_verification(row, step, file.get("id"), verification, outcome, verification.get("reason_code") or outcome)
            state["verification_result"] = outcome
            if not valid:
                substate = "verification_error" if outcome == "VERIFICATION_ERROR" else "verification_failed"
                self._save(row, step, state, token, substate, "SIGNATURE_VERIFICATION_FAILED", signer)
                return {"is_valid": False, "completed": False, "result": outcome, "message": VERIFICATION_MESSAGES[outcome]}
            try:
                if signer == "participant" and requires_signature:
                    self.documents.record_composite_participant_signature(row, step, data, filename, instance, verification)
                elif signer == "office":
                    self.documents.record_composite_office_signature(row, step, filename, instance)
            except ValueError:
                self.documents.submission_document_service.record_document_metadata(**pending_metadata, signed=requires_signature,
                    status="rejected_no_capacity", signature_status=str(verification.get("validation_status") or "").lower(), signature_validation_result=verification)
                self._save(row, step, state, token, "verification_failed", "DOCUMENT_ACCEPTANCE_FAILED", signer)
                raise
            signatures[signer] = filename
            self._save(row, step, state, token, f"{signer}_signed", "SIGNATURE_VERIFIED" if requires_signature else "DOCUMENT_UPLOAD_ACCEPTED", signer)
            required = (["participant"] if policy["upload_required"] else []) + (["office"] if policy["office_signature"] else [])
            completed = all(all(state["signatures"].get(f.get("training_key") or "document", {}).get(role) for role in required) for f in files)
            if completed:
                self._save(row, step, state, token, "signed", "DOCUMENT_SIGNED", signer)
                self._complete(row, step, state, token)
            else:
                waiting_office = policy["office_signature"] and all(state["signatures"].get(f.get("training_key") or "document", {}).get("participant") for f in files)
                self._save(row, step, state, token, "awaiting_office_signature" if waiting_office else "awaiting_signature", "DOCUMENT_SIGNATURE_REQUESTED")
        finally:
            self.repository.release_document_operation(row["submission_id"], token)
        if completed:
            fresh = self.repository.get_by_id(row["submission_id"])
            self.documents.workflow_service.advance_after_action(fresh, definition)
        return {"is_valid": True, "completed": completed, "result": "VALID_SIGNATURE", "message": VERIFICATION_MESSAGES["VALID_SIGNATURE"]}

    @staticmethod
    def _log_verification(row, step, file_id, verification, result, reason_code):
        current_app.logger.info(
            "signature_verification_result submission_id=%s document_id=%s file_id=%s result=%s provider=%s reason_code=%s "
            "detected_signature_type=%s verifier_backend=%s provider_allowed=%s signature_count=%s "
            "cryptographic_valid=%s document_integrity_valid=%s certificate_chain_valid=%s timestamp_valid=%s "
            "upload_sha256=%s storage_sha256=%s verifier_sha256=%s",
            row["submission_id"], step["document_id"], file_id, result, verification.get("provider") or "unknown", reason_code,
            verification.get("detected_signature_type"), verification.get("verifier_backend"), verification.get("provider_allowed"),
            verification.get("signature_count"), verification.get("cryptographic_valid"), verification.get("document_integrity_valid"),
            verification.get("certificate_chain_valid"), verification.get("timestamp_valid"),
            verification.get("upload_sha256"), verification.get("storage_sha256"), verification.get("verifier_sha256"))

    def view(self, row, definition, *, signer="participant"):
        step = self.documents.composite_step(row, definition)
        if not step:
            return None
        document = self.documents.get_document_by_id(definition, step["document_id"])
        policy = document_policy(step, document)
        state = document_step_state(row, step["id"])
        substate = state.get("substate", "generating")
        collect_data = (self.needs_data(document, state) or substate == "collect_data" and not state.get("data_confirmed")
            or substate == "failed" and bool(document.get("fields")) and not state.get("files"))
        if collect_data:
            substate = "collect_data"
        busy = float(document_states(row).get("document_operation_until") or 0) > time.time()
        files = []
        for source in [] if collect_data else state.get("files", []):
            signatures = state.get("signatures", {}).get(source.get("training_key") or "document", {})
            filename = (signatures.get("participant") or source.get("filename")) if signer == "office" else source.get("filename")
            can_upload = not state.get("completed") and substate not in {"generating", "failed"} and not busy and not signatures.get(signer)
            can_upload = can_upload and (policy["upload_required"] if signer == "participant" else policy["office_signature"] and (not policy["upload_required"] or bool(signatures.get("participant"))))
            files.append({"filename": filename if policy["download"] or signer == "office" else "", "instance": source.get("training_key", ""),
                          "label": self._public_file_label(row, document, source),
                          "can_upload": bool(can_upload), "signed": bool(signatures.get(signer))})
        status = document_step_status(row, step)
        if collect_data:
            status.update(title=document["label"], message="Uzupełnij dane wymagane do przygotowania deklaracji.", substate="collect_data")
        return {"step_id": step["id"], "label": step.get("admin_label") or document["label"], "document_id": step["document_id"],
                "substate": substate, "files": files, "policy": policy, "status": status,
                "form_definition": current_app.extensions["services"].declaration_flow_service.document_fields_definition(
                    current_app.extensions["services"].declaration_flow_service.operational_document(document, row, self.repository)) if collect_data else None,
                "instruction": (step.get("document_instructions") or {}).get(substate) or {},
                "values": dict(row),
                "can_retry": not busy and (substate in {"failed", "generating"} or bool(state.get("completed")))}

    def completed_files(self, row, definition):
        files = []
        for step in definition.get("workflow", {}).get("steps", []):
            state = document_step_state(row, step.get("id"))
            if not is_document_step(step) or not state.get("completed"):
                continue
            document = self.documents.get_document_by_id(definition, step["document_id"])
            if not document_policy(step, document)["download"]:
                continue
            for source in state.get("files", []):
                signatures = state.get("signatures", {}).get(source.get("training_key") or "document", {})
                filename = signatures.get("office") or signatures.get("participant") or source.get("filename")
                if filename:
                    files.append({"step_id": step["id"], "label": self._public_file_label(row, document, source), "filename": filename})
        return files
