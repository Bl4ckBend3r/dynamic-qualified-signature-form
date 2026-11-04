from copy import deepcopy
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import json

import pytest
from pypdf import PdfWriter
from sqlalchemy import select
from werkzeug.datastructures import FileStorage, MultiDict

from models import FormSubmission, FormVersion, SubmissionTraining, SubmissionWorkflowEvent
from services.documents.document_workflow_service import (
    document_item_state,
    document_step_state,
    validate_document_step,
)
from services.process_instruction_service import build_process_instruction_view
from services.public_submission_status_service import build_public_submission_status, build_readonly_submission_status
from services.workflow_service import WorkflowTransitionError
from test_admin_panel import admin_app, create_form, create_user, login
from test_signature_verifier import certificate_chain_signed_pdf_factory, signed_pdf_factory


@pytest.fixture
def composite_env(admin_app, monkeypatch):
    services = admin_app.extensions['services']
    # This fixture exercises canonical readback, like Nextcloud. The shared legacy
    # fake otherwise saves PDFs only by filename and cannot read their metadata path.
    from services.file_metadata import resolve_pdf_storage_path
    storage = services.document_service.storage
    save_pdf = storage.save_pdf
    def save_with_canonical_path(slug, filename, pdf_bytes, **kwargs):
        save_pdf(slug, filename, pdf_bytes, **kwargs)
        storage.write_bytes(resolve_pdf_storage_path(storage, slug, filename, **kwargs), pdf_bytes)
    monkeypatch.setattr(storage, 'save_pdf', save_with_canonical_path)
    pdf = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(pdf)
    pdf_bytes = pdf.getvalue()
    generated = []

    def render(**kwargs):
        generated.append(kwargs)
        return pdf_bytes

    monkeypatch.setattr(services.document_service.pdf_render_service, 'render_document_pdf_bytes', render)
    verification = {'is_signed': True, 'cryptographically_valid': True, 'integrity_ok': True,
                    'validation_status': 'VALID', 'signature_count': 1, 'is_allowed_signature': True, 'signature_type': 'mszafir'}
    monkeypatch.setattr(services.document_signing_service, 'verifier', lambda _: dict(verification))

    def create(document_id='declaration', **options):
        slug = f'composite-{uuid4().hex[:8]}'
        form_id = create_form(admin_app, slug=slug)
        definition = {
            'title': 'Composite', 'fields': [],
            'documents': [{'id': document_id, 'label': 'Dokument testowy', 'kind': 'generated_pdf',
                           'template_html': '<p>Dokument {{ submission_id }}</p>', 'filename_pattern': '{submission_id}-document.pdf'}],
            'workflow': {'flow_mode': 'explicit', 'schema_version': 2, 'initial_step': 'intake',
                'steps': [
                    {'id': 'intake', 'admin_label': 'Złożenie', 'user_label': 'Złożenie', 'status': 'FORM_SUBMITTED', 'stage_type': 'user_action', 'type': 'form_submit', 'next': 'paper'},
                    {'id': 'paper', 'admin_label': 'Dokument', 'user_label': 'Dokument', 'status': 'DECLARATION_WAITING_FOR_SIGNATURE',
                     'stage_type': 'document', 'type': 'document', 'document_lifecycle': 'composite', 'document_id': document_id,
                     'document_options': options, 'action': 'none', 'next': 'review',
                     'document_instructions': {'ready': {'description': '<p>Pobierz swój plik.</p>'}, 'verification_failed': {'description': '<p>Podpisz ponownie.</p>'}}},
                    {'id': 'review', 'admin_label': 'Decyzja', 'user_label': 'Decyzja', 'status': 'WAITING_FOR_OFFICER_DECISION', 'stage_type': 'decision', 'type': 'manual_decision', 'decision_scope': 'submission', 'decision_name': 'Decyzja'},
                    {'id': 'done', 'admin_label': 'Koniec', 'user_label': 'Koniec', 'status': 'COMPLETED', 'stage_type': 'final', 'final': True}],
                'decision_types': [{'code': code, 'label': code, 'scope': 'submission', 'step_id': 'review', 'target_step': 'done', 'semantic_category': 'neutral'} for code in ['yes', 'no']]}}
        public_id = str(uuid4())
        with services.submission_repository.session_factory() as db:
            version = FormVersion(form_id=form_id, version_major=1, version_minor=0, version_label='1.0', status='published', definition_json=definition)
            db.add(version)
            db.flush()
            model = FormSubmission(submission_id=public_id, form_slug=slug, form_version_id=version.id,
                access_token=uuid4().hex, process_status='FORM_SUBMITTED', workflow_stage='intake', workflow_step='intake')
            db.add(model)
            db.commit()
        repo = services.submission_repository
        env = SimpleNamespace(app=admin_app, services=services, repo=repo, definition=definition, public_id=public_id, form_id=form_id,
            document=services.document_service.document_workflow, generated=generated, pdf=pdf_bytes, verification=verification)
        env.row = lambda: repo.get_by_id(public_id)
        env.step_state = lambda: document_step_state(env.row(), 'paper')
        env.state = lambda: document_item_state(
            env.row(), 'paper', document_id, primary_document_id=document_id)
        env.file = lambda suffix=b'\n% participant signature\n': FileStorage(stream=BytesIO(pdf_bytes + suffix), filename='signed.pdf', content_type='application/pdf')
        env.enter = lambda: services.workflow_service.advance_after_action(env.row(), definition)
        return env
    with admin_app.app_context():
        yield create


def configure_document_fields(env):
    document = env.definition['documents'][0]
    document.update(label='Deklaracja uczestnictwa', form_title='Deklaracja uczestnictwa',
        fields=[{'name': 'deklaracja_18_lat', 'label': 'Ukończyłem 18 lat', 'type': 'radio', 'options': ['Tak', 'Nie'], 'required': True},
            {'name': 'wybor_szkolen', 'label': 'Wybór szkoleń', 'type': 'training_selection', 'required': True,
             'max_total_amount': '2000', 'catalog': [{'id': 'course-a', 'name': 'Szkolenie A', 'price': '100', 'capacity': 10}]},
            {'name': 'declaration_note', 'label': 'Uwagi', 'type': 'text'}],
        template_source='builder', builder_document={'version': 1, 'blocks': [
            {'id': 'text', 'type': 'paragraph', 'content': 'Wiek: {{ deklaracja_18_lat }}. Uwagi: {{ declaration_note }}.'}]})
    with env.repo.session_factory() as db:
        db.get(FormVersion, env.row()['form_version_id']).definition_json = deepcopy(env.definition)
        from models import Form
        db.get(Form, env.form_id).definition_json = deepcopy(env.definition)
        db.commit()


@pytest.mark.parametrize("answer,failure_action,expected_step", [
    ("Tak", "auto_reject", "paper"),
    ("Nie", "auto_reject", "auto_rejected"),
    ("Nie", "officer_decision", "officer_decision"),
])
def test_document_qualification_waits_for_saved_data_and_uses_submission_version(
    composite_env, monkeypatch, answer, failure_action, expected_step,
):
    env = composite_env("eligibility_certificate")
    env.definition["documents"][0]["fields"] = [
        {"name": "adult", "type": "radio", "options": ["Tak", "Nie"], "required": True},
    ]
    env.definition["documents"].append({"id": "later_certificate", "fields": [
        {"name": "later_answer", "type": "text"},
    ]})
    env.definition["qualification_conditions"] = {"enabled": True, "conditions": [
        {"field_name": "adult", "operator": "equals", "expected_value": "Tak", "failure_action": failure_action},
        {"field_name": "later_answer", "operator": "equals", "expected_value": "Tak", "failure_action": "auto_reject"},
    ]}
    snapshot = deepcopy(env.definition)
    with env.repo.session_factory() as db:
        db.get(FormVersion, env.row()["form_version_id"]).definition_json = snapshot
        db.commit()
    sent = []
    monkeypatch.setattr(env.services.mail_dispatch_service, "dispatch_auto_rejected_by_condition",
                        lambda public_id, evaluation: sent.append(public_id))

    # No document data yet: recheck uses the submission version and skips both conditions.
    before = env.services.submission_service.reevaluate_qualification(env.public_id, {"fields": []})
    assert before["passed"] and before["evaluation"]["results"] == []
    env.enter()
    assert env.row()["workflow_step"] == "paper"
    invalid = env.document.save_fields(env.row(), env.definition, "paper", MultiDict(), generate=False)
    assert not invalid.success
    assert len(env.row()["data_json"]["_qualification_history"]) == 1

    saved = env.document.save_fields(env.row(), env.definition, "paper", MultiDict({"adult": answer}), generate=False)
    assert saved.success
    row = env.row()
    assert row["workflow_step"] == expected_step
    assert row["data_json"]["adult"] == answer
    evaluation = row["data_json"]["_qualification"]
    assert [item["field_name"] for item in evaluation["results"]] == ["adult"]
    assert evaluation["passed"] is (answer == "Tak")
    assert len(row["data_json"]["_qualification_history"]) == 2
    assert not env.generated
    assert sent == ([env.public_id] if expected_step == "auto_rejected" else [])
    recheck = env.services.submission_service.reevaluate_qualification(env.public_id, {"fields": []})
    assert [item["field_name"] for item in recheck["evaluation"]["results"]] == ["adult"]
    assert env.row()["workflow_step"] == expected_step
    assert sent == ([env.public_id] if expected_step == "auto_rejected" else [])
    with env.repo.session_factory() as db:
        assert db.get(FormVersion, row["form_version_id"]).definition_json == snapshot


def configure_participant_document_stage(env, documents):
    # Given: a generic participant document stage.
    step = env.definition["workflow"]["steps"][1]
    step.update(stage_type="documents", type="document", requires_user_action=True,
        status="WAITING_FOR_USER_ACTION", action="none", document_lifecycle="group")
    for key in ("document_id", "document_options"):
        step.pop(key, None)
    env.definition["documents"] = documents
    with env.repo.session_factory() as db:
        db.get(FormVersion, env.row()["form_version_id"]).definition_json = deepcopy(env.definition)
        db.commit()


def configure_generic_document_stage(env, documents, *, step_id="paper"):
    step = env.definition["workflow"]["steps"][1]
    step.update(id=step_id, stage_type="documents", type="document", requires_user_action=True,
                status="WAITING_FOR_USER_ACTION", action="none", document_lifecycle="group")
    for key in ("document_id", "document_options", "document_instructions"):
        step.pop(key, None)
    env.definition["workflow"]["steps"][0]["next"] = step_id
    env.definition["documents"] = [{**document, "workflow_step": step_id} for document in documents]
    with env.repo.session_factory() as db:
        db.get(FormVersion, env.row()["form_version_id"]).definition_json = deepcopy(env.definition)
        db.commit()


def test_single_declaration_keeps_its_collect_data_flow(composite_env):
    # Given: a historical single-document composite step with declaration fields.
    env = composite_env()
    configure_document_fields(env)
    # When: the participant enters the step.
    env.enter()
    view = env.document.view(env.row(), env.definition)
    # Expected: the existing single-document form remains available.
    assert view["substate"] == "collect_data"
    assert view.get("group")
    field_names = {
        field["name"]
        for field in view["form_definition"]["fields"]
        if field.get("name")
    }

    assert "deklaracja_18_lat" in field_names
    assert "wybor_szkolen" not in field_names
    assert "wybor_szkolen" not in {
        field["name"]
        for field in view["form_definition"]["fields"]
        if field.get("name")
    }


def test_generic_document_stage_uses_assignment_for_single_declaration(composite_env):
    env = composite_env()
    document = deepcopy(env.definition["documents"][0])
    document["fields"] = [{"name": "answer", "label": "Odpowiedź", "type": "text", "required": True,
                           "document_usage": {"declaration": True}}]
    configure_generic_document_stage(env, [document], step_id="docs_1")
    assert validate_document_step(env.definition["workflow"]["steps"][1], env.definition) == []
    env.enter()
    state = document_step_state(env.row(), "docs_1")
    assert state["documents"]["declaration"]["substate"] == "collect_data"
    assert build_public_submission_status(env.row(), form_config=env.definition)["can_fill_declaration"]
    view = env.document.view(env.row(), env.definition)
    assert view["group"] and view["form_document_id"] == "declaration"
    assert {field["name"] for field in view["form_definition"]["fields"]} == {"answer"}
    result = env.document.save_fields(env.row(), env.definition, "docs_1", {"answer": "Tak"},
                                      document_id="declaration")
    assert result.success
    state = document_step_state(env.row(), "docs_1")
    assert state["documents"]["declaration"]["generated_filename"]
    assert not state["documents"]["declaration"].get("completed")
    status = build_public_submission_status(env.row(), form_config=env.definition)
    assert status["can_download_declaration"] and status["can_upload_signed_declaration"]


def test_generic_document_stage_custom_generation_with_declaration_step_id(composite_env):
    env = composite_env()
    declaration = deepcopy(env.definition["documents"][0])
    custom = {"id": "osw_wizerunek", "label": "Oświadczenie wizerunkowe", "kind": "generated_pdf",
              "template_html": "<p>Wizerunek</p>", "required": True, "signature_required": True,
              "order": 2}
    configure_generic_document_stage(env, [declaration, custom], step_id="declaration")
    env.enter()
    client = env.app.test_client()
    row = env.row()
    page = client.get(f"/do-podpisania?submission_id={env.public_id}&token={row['access_token']}")
    assert page.status_code == 200
    assert "Oświadczenie wizerunkowe" in page.text and "Dokument testowy" in page.text
    csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    url = (f"/document-steps/{row['form_slug']}/{env.public_id}/declaration/generate/osw_wizerunek"
           f"?token={row['access_token']}")
    response = client.post(url, data={"csrf_token": csrf})
    assert response.status_code == 302
    item = document_step_state(env.row(), "declaration")["documents"]["osw_wizerunek"]
    assert item["generated_filename"]
    assert any(file["document_id"] == "osw_wizerunek" and file["workflow_step_at_upload"] == "declaration"
               for file in env.services.submission_document_service.list_documents(env.public_id))
    assert env.document.download(env.row(), env.definition, "declaration", item["generated_filename"]) == env.pdf
    with pytest.raises(ValueError, match="nie jest dostępny"):
        env.document.generate_group_document(env.row(), env.definition, "declaration", "other_step")


def test_generic_document_stage_single_agreement_with_office_signature(composite_env):
    env = composite_env("agreement")
    agreement = deepcopy(env.definition["documents"][0])
    agreement["lifecycle"] = {"generate": True, "download": True, "upload_required": True,
                              "participant_signature": True, "office_signature": True,
                              "signature_verification": True}
    configure_generic_document_stage(env, [agreement], step_id="participant_contract")
    env.enter()
    item = document_step_state(env.row(), "participant_contract")["documents"]["agreement"]
    assert item["generated_filename"] and not item.get("completed")
    participant = env.document.upload(env.row(), env.definition, "participant_contract", env.file(),
                                      document_id="agreement")
    assert participant["is_valid"] and not item.get("completed")
    assert env.row()["workflow_step"] == "participant_contract"
    env.verification["signature_count"] = 2
    office = env.document.upload(env.row(), env.definition, "participant_contract",
        env.file(b"\n% participant signature\n\n% office signature\n"),
        signer="office", document_id="agreement")
    assert office["is_valid"] and office["completed"]
    assert env.row()["workflow_step"] == "review"


@pytest.mark.parametrize("with_optional", [False, True])
def test_generic_document_stage_keeps_agreements_per_training(composite_env, with_optional):
    env = composite_env("agreement")
    agreement = deepcopy(env.definition["documents"][0])
    agreement.update(generation_mode="per_training", repeat_over="selected_trainings",
                     repeat_item_alias="training")
    agreement["lifecycle"] = {"generate": True, "download": True, "upload_required": True,
                              "participant_signature": True, "office_signature": True,
                              "signature_verification": True}
    documents = [agreement]
    if with_optional:
        documents.append({"id": "attachment", "label": "Załącznik", "kind": "uploaded_file",
                          "required": False, "signature_required": False})
    configure_generic_document_stage(env, documents, step_id="contracts_1")
    env.repo.update(env.public_id, {"selected_trainings": json.dumps([
        {"id": "course-a", "name": "Szkolenie A", "price": "100"},
        {"id": "course-b", "name": "Szkolenie B", "price": "200"}], ensure_ascii=False)})
    env.enter()
    item = document_step_state(env.row(), "contracts_1")["documents"]["agreement"]
    assert len(item["files"]) == 2
    assert {file["training_key"] for file in item["files"]} == {"course-a", "course-b"}
    assert not item["completed"]
    view = env.document.view(env.row(), env.definition)
    assert view["group"] and len(view["files"]) == (3 if with_optional else 2)
    for file in item["files"]:
        assert env.document.download(env.row(), env.definition, "contracts_1", file["filename"]) == env.pdf
    metadata = [file for file in env.services.submission_document_service.list_documents(env.public_id)
                if file["document_id"] == "agreement"]
    assert {file["training_key"] for file in metadata} == {"course-a", "course-b"}
    with env.repo.session_factory() as db:
        for key, name in (("course-a", "Szkolenie A"), ("course-b", "Szkolenie B")):
            db.add(SubmissionTraining(submission_id=env.row()["id"], training_id=key,
                training_name_snapshot=name, training_price_snapshot="100.00",
                training_snapshot={"id": key, "name": name, "price": "100"}, status="selected"))
        db.commit()
    client = env.app.test_client()
    row = env.row()
    page = client.get(f"/do-podpisania?submission_id={env.public_id}&token={row['access_token']}")
    assert page.status_code == 200 and "agreement::course-a" in page.text and "agreement::course-b" in page.text
    csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    bulk = client.post(f"/document-steps/{row['form_slug']}/{env.public_id}/contracts_1/bulk-upload"
        f"?token={row['access_token']}", data={"csrf_token": csrf,
            "document_ids": ["agreement::course-a", "agreement::course-b"],
            "document_files": [(BytesIO(env.pdf + b"\n% participant signature\n"), "a.pdf"),
                               (BytesIO(env.pdf + b"\n% participant signature\n"), "b.pdf")]},
        content_type="multipart/form-data")
    assert bulk.status_code == 302
    assert env.row()["workflow_step"] == "contracts_1"
    env.verification["signature_count"] = 2
    for key in ("course-a", "course-b"):
        uploaded = env.document.upload(env.row(), env.definition, "contracts_1",
            env.file(b"\n% participant signature\n\n% office signature\n"),
            signer="office", document_id="agreement", instance=key)
        assert uploaded["is_valid"]
    assert env.row()["workflow_step"] == "review"


def test_collection_view_neutralizes_historical_failure_while_waiting_for_selection(composite_env):
    env = composite_env("agreement")
    agreement = deepcopy(env.definition["documents"][0])
    agreement.update(generation_mode="per_training", repeat_over="selected_trainings",
                     repeat_item_alias="training")
    configure_generic_document_stage(env, [agreement], step_id="contracts_1")
    env.enter()

    stored_states = env.row()["document_states"]
    states = json.loads(stored_states) if isinstance(stored_states, str) else deepcopy(stored_states)
    step_state = states["workflow_documents"]["contracts_1"]
    step_state["substate"] = "failed"
    step_state["documents"]["agreement"].update(
        substate="failed", error="Historical generation failure")
    env.repo.update(env.public_id, {"document_states": states})

    view = env.document.view(env.row(), env.definition)
    assert view["substate"] == "ready"
    assert view["files"] == []
    assert not view["can_retry"]
    assert document_item_state(env.row(), "contracts_1", "agreement")["substate"] == "failed"

    row = env.row()
    page = env.app.test_client().get(
        f"/do-podpisania?submission_id={env.public_id}&token={row['access_token']}")
    assert page.status_code == 200
    assert 'data-substate="ready"' in page.text
    assert "błąd przygotowania dokumentu" not in page.text.lower()


def test_admin_assignment_keeps_reserved_document_identity(composite_env):
    from services.admin_form_service import build_form_definition_from_admin_form
    env = composite_env()
    workflow = deepcopy(env.definition["workflow"])
    step = workflow["steps"][1]
    step.update(id="docs_1", stage_type="document", type="document", document_lifecycle="group",
                document_id="", document_options={}, action="none", requires_user_action=True)
    workflow["steps"][0]["next"] = "docs_1"
    fields = MultiDict({"active_tab": "workflow", "workflow_builder_json": json.dumps(workflow),
        "workflow_initial_step": "intake", "requires_declaration": "on",
        "declaration_template_html": "<p>Deklaracja</p>", "documents_config_json": "[]",
        "reserved_documents_json": '[{"id":"declaration","workflow_step":"docs_1","order":3}]'})
    saved = build_form_definition_from_admin_form(env.definition, fields)
    declaration = next(item for item in saved["documents"] if item["id"] == "declaration")
    assert declaration["workflow_step"] == "docs_1" and declaration["order"] == 3
    assert saved["workflow"]["steps"][1]["id"] == "docs_1"


def test_declaration_with_other_documents_collects_generates_downloads_and_waits(composite_env):
    # Given: an assigned declaration field, an excluded field, and two other
    # documents on the same stage (one required and one optional).
    env = composite_env()
    env.definition["fields"] = [
        {"name": "declaration_answer", "label": "Odpowiedź deklaracji", "type": "text",
         "required": True, "document_usage": {"declaration": True}},
        {"name": "other_answer", "label": "Inna odpowiedź", "type": "text",
         "required": True, "document_usage": {"declaration": False}},
    ]
    env.definition["documents"].extend([
        {"id": "custom_a", "label": "Załącznik A", "kind": "uploaded_file",
         "workflow_step": "paper", "required": True, "signature_required": False},
        {"id": "custom_b", "label": "Załącznik B", "kind": "uploaded_file",
         "workflow_step": "paper", "required": False, "signature_required": False},
        {"id": "other_step", "label": "Inny etap", "kind": "uploaded_file",
         "workflow_step": "later", "required": False},
    ])
    with env.repo.session_factory() as db:
        db.get(FormVersion, env.row()["form_version_id"]).definition_json = deepcopy(env.definition)
        db.commit()
    env.repo.update(env.public_id, {"data_json": {
        "other_answer": "Odpowiedź z formularza",
        "ulica": "Przykładowa", "nr_budynku": "12", "nr_lokalu": "3",
        "kod_pocztowy": "65-001", "miejscowosc": "Zielona Góra"}})
    env.enter()
    assert env.step_state()["documents"]["declaration"]["substate"] == "collect_data"
    assert not any(file["document_id"] == "declaration" for file in
                   env.services.submission_document_service.list_documents(env.public_id))
    with pytest.raises(ValueError, match="uzupełnij dane"):
        env.document.generate_group_document(env.row(), env.definition, "paper", "declaration")

    env.app.config.update(PUBLIC_CSRF_ENABLED=True)
    client = env.app.test_client()
    row = env.row()
    page_url = f"/do-podpisania?submission_id={env.public_id}&token={row['access_token']}"
    page = client.get(page_url)
    assert page.status_code == 200
    assert "declaration_answer" in page.text and "other_answer" not in page.text
    csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    incomplete_data = dict(env.row()["data_json"])
    incomplete_data["_form_version_upgrade"] = {
        "missing_fields": ["declaration_answer"],
        "invalid_training_ids": [],
        "missing_documents": [],
    }
    env.repo.update(env.public_id, {"data_json": incomplete_data})
    fields_url = (f"/document-steps/{row['form_slug']}/{env.public_id}/paper/fields"
                  f"?document_id=declaration&token={row['access_token']}")
    wrong = client.post(fields_url.replace("document_id=declaration", "document_id=other_step"),
                        data={"csrf_token": csrf, "declaration_answer": "Tak"})
    assert wrong.status_code == 404
    assert not env.step_state()["documents"]["declaration"].get("data_confirmed")
    invalid = client.post(fields_url.replace("document_id=declaration", "document_id=unknown"),
                          data={"csrf_token": csrf, "declaration_answer": "Tak"})
    assert invalid.status_code == 404

    # When: the participant generates the declaration with one submit.
    saved = client.post(fields_url, data={"csrf_token": csrf, "declaration_answer": "Tak"})
    assert saved.status_code == 302
    state = env.step_state()
    declaration = state["documents"]["declaration"]
    assert declaration["data_confirmed"] is True and declaration["data_sha256"]
    assert declaration["generated_filename"] and not declaration.get("regenerate")
    assert not declaration.get("completed")
    assert not state.get("completed")
    assert not state["documents"]["custom_a"].get("completed")
    assert not state["documents"]["custom_b"].get("completed")
    metadata = next(file for file in env.services.submission_document_service.list_documents(env.public_id)
                    if file["filename"] == declaration["generated_filename"])
    assert (metadata["document_id"], metadata["workflow_step_at_upload"], metadata["status"]) == (
        "declaration", "paper", "generated")
    assert metadata["storage_path"]
    assert len(env.generated) == 1
    assert env.generated[0]["context"]["declaration_answer"] == "Tak"
    assert "other_answer_display" not in env.generated[0]["context"]
    assert env.generated[0]["context"]["participant_address"] == (
        "ul. Przykładowa 12/3, 65-001 Zielona Góra")

    # Expected: the group card offers the exact saved PDF, and only both
    # required uploads complete the stage.
    group_page = client.get(page_url)
    assert group_page.status_code == 200
    download_url = (f"/document-steps/{row['form_slug']}/{env.public_id}/paper/download/declaration/"
                    f"{declaration['generated_filename']}?token={row['access_token']}")
    assert download_url.split("?", 1)[0] in group_page.text
    assert client.get(download_url).data == env.pdf
    assert client.get(download_url.replace("/download/declaration/", "/download/custom_a/")).status_code == 404
    bulk_url = f"/document-steps/{row['form_slug']}/{env.public_id}/paper/bulk-upload?token={row['access_token']}"
    signed = client.post(bulk_url, data={"csrf_token": csrf, "document_ids": ["declaration"],
        "document_files": [(BytesIO(env.pdf + b"\n% signed declaration"), "signed.pdf")]},
        content_type="multipart/form-data")
    assert signed.status_code == 302
    assert env.step_state()["documents"]["declaration"]["completed"] is True
    assert env.row()["workflow_step"] == "paper"
    env.document.start(env.row(), env.definition)
    assert len(env.generated) == 1
    assert env.step_state()["documents"]["declaration"]["completed"] is True
    custom = client.post(bulk_url, data={"csrf_token": csrf, "document_ids": ["custom_a"],
        "document_files": [(BytesIO(env.pdf), "custom.pdf")]}, content_type="multipart/form-data")
    assert custom.status_code == 302
    assert env.row()["workflow_step"] == "review"


def test_participant_stage_generated_and_own_pdf_bulk_flow(composite_env):
    # Given: one generated PDF and one participant supplied PDF on a normal
    # participant stage; both require verified participant signatures.
    env = composite_env()
    documents = [
        {"id": "statement_a", "label": "Oświadczenie A", "kind": "generated_pdf",
         "participant_label": "Oświadczenie uczestnika",
         "template_html": "<p>{{ submission_id }}</p>", "workflow_step": "paper", "required": True,
         "signature_required": True, "lifecycle": {"generate": True, "download": True,
             "upload_required": True, "participant_signature": True, "signature_verification": True}},
        {"id": "statement_b", "label": "Oświadczenie B", "kind": "uploaded_file",
         "workflow_step": "paper", "required": True, "signature_required": True,
         "lifecycle": {"generate": False, "upload_required": True,
             "participant_signature": True, "signature_verification": True}},
        {"id": "future_statement", "label": "Późniejszy dokument", "kind": "uploaded_file",
         "workflow_step": "later", "required": True, "signature_required": True},
    ]
    env.definition["workflow"]["steps"].insert(-1, {"id": "later", "stage_type": "user_action",
        "type": "user_action", "status": "WAITING_FOR_USER_ACTION", "next": "done"})
    configure_participant_document_stage(env, documents)
    env.enter()
    assert env.row()["workflow_step"] == "paper"
    env.app.config.update(PUBLIC_CSRF_ENABLED=True, WTF_CSRF_ENABLED=True)
    client = env.app.test_client()
    row = env.row()
    page = client.get(f"/do-podpisania?submission_id={env.public_id}&token={row['access_token']}")
    assert page.status_code == 200
    assert "statement_a" in page.text and "statement_b" in page.text
    assert 'data-bulk-document-upload' not in page.text and 'data-document-upload' not in page.text
    assert 'Wgraj podpisane dokumenty' not in page.text
    for document_id in ('statement_a', 'statement_b'):
        card = page.text.split(f'data-document-card="{document_id}"', 1)[1].split('</article>', 1)[0]
        assert f'name="document_ids" value="{document_id}"' in card
        assert 'name="document_files"' in card and '<select' not in card
        assert 'Wgraj podpisany dokument (PDF)' in card
    csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    generate_url = f"/document-steps/{row['form_slug']}/{env.public_id}/paper/generate/statement_a?token={row['access_token']}"
    # When: the first document is generated and downloaded.
    assert client.post(generate_url, data={"csrf_token": csrf, "access_token": row["access_token"]}).status_code == 302
    generated = next(file for file in env.services.submission_document_service.list_documents(env.public_id)
                     if file["document_id"] == "statement_a" and file["status"] == "generated")
    assert client.get(f"/document-steps/{row['form_slug']}/{env.public_id}/paper/download/{generated['filename']}?token={row['access_token']}").status_code == 200
    assert env.row()["workflow_step"] == "paper"
    generated_page = client.get(f"/do-podpisania?submission_id={env.public_id}&token={row['access_token']}")
    first_card = generated_page.text.split('data-document-card="statement_a"', 1)[1].split('</article>', 1)[0]
    assert "Oświadczenie uczestnika" in first_card
    assert "Oświadczenie A" not in first_card
    assert first_card.index('Pobierz dokument') < first_card.index('Wgraj podpisany dokument (PDF)')
    assert env.document.view(env.row(), env.definition, signer="office")["files"][0]["label"] == "Oświadczenie uczestnika"
    bulk_url = f"/document-steps/{row['form_slug']}/{env.public_id}/paper/bulk-upload?token={row['access_token']}"
    without_csrf = client.post(bulk_url, data={"access_token": row["access_token"],
        "document_ids": ["statement_b"], "document_files": [(BytesIO(env.pdf), "b.pdf")]},
        content_type="multipart/form-data")
    assert without_csrf.status_code == 400
    without_access = client.post(bulk_url.split("?", 1)[0], data={"csrf_token": csrf,
        "document_ids": ["statement_b"], "document_files": [(BytesIO(env.pdf), "b.pdf")]},
        content_type="multipart/form-data")
    assert without_access.status_code in {403, 404}
    assert not any(file["document_id"] == "statement_b"
                   for file in env.services.submission_document_service.list_documents(env.public_id))
    assert client.post(bulk_url, data={"csrf_token": csrf, "access_token": row["access_token"],
        "document_ids": ["future_statement"], "document_files": [(BytesIO(env.pdf), "later.pdf")]},
        content_type="multipart/form-data").status_code == 404
    # When: an unsigned PDF is supplied for A, it is rejected and can be retried.
    env.verification.update(is_signed=False, validation_status="UNSIGNED", signature_count=0)
    rejected = client.post(bulk_url, data={"csrf_token": csrf, "access_token": row["access_token"],
        "document_ids": ["statement_a"], "document_files": [(BytesIO(env.pdf), "unsigned.pdf")]},
        content_type="multipart/form-data", follow_redirects=True)
    assert rejected.status_code == 200 and env.row()["workflow_step"] == "paper"
    assert not env.step_state().get("documents", {}).get("statement_a", {}).get("completed")
    env.verification.update(is_signed=True, validation_status="VALID", signature_count=1)
    # When: the participant uses the upload in the second card first.
    second = client.post(bulk_url, data={"csrf_token": csrf, "access_token": row["access_token"],
        "document_ids": ["statement_b"],
        "document_files": [(BytesIO(env.pdf + b"\n% signed B"), "b.pdf")]}, content_type="multipart/form-data")
    assert second.status_code == 302 and env.row()["workflow_step"] == "paper"
    state = env.step_state()["documents"]
    assert not state.get("statement_a", {}).get("completed")
    assert state["statement_b"]["completed"]
    assert env.document.view(env.row(), env.definition)["ready_count"] == 1
    status_page = client.get(f"/do-podpisania?submission_id={env.public_id}&token={row['access_token']}")
    assert "1 z 2 wymaganych dokumentów ukończone" in status_page.text
    assert 'data-document-card="statement_a"' in status_page.text
    assert 'data-document-card="statement_b"' in status_page.text
    assert {file["document_id"] for file in env.services.submission_document_service.list_documents(env.public_id)
            if file["status"] == "signed"} == {"statement_b"}
    # Expected: the first document completes only after its own upload.
    first = client.post(bulk_url, data={"csrf_token": csrf, "access_token": row["access_token"],
        "document_ids": ["statement_a"],
        "document_files": [(BytesIO(env.pdf + b"\n% signed A"), "a.pdf")]}, content_type="multipart/form-data")
    assert first.status_code == 302 and env.row()["workflow_step"] == "review"
    files = env.services.submission_document_service.list_documents(env.public_id)
    assert {file["document_id"] for file in files if file["status"] == "signed"} == {"statement_a", "statement_b"}
    assert next(file["label"] for file in env.document.completed_files(env.row(), env.definition)
                if file["document_id"] == "statement_a") == "Oświadczenie uczestnika"


def test_document_cards_fit_mobile_and_both_themes(composite_env, tmp_path):
    playwright_api = pytest.importorskip("playwright.sync_api")
    env = composite_env()
    configure_participant_document_stage(env, [
        {"id": document_id, "label": label, "kind": "generated_pdf",
         "template_html": "<p>{{ submission_id }}</p>", "workflow_step": "paper",
         "required": True, "signature_required": True,
         "lifecycle": {"generate": True, "download": True, "upload_required": True,
                       "participant_signature": True, "signature_verification": True}}
        for document_id, label in (("statement_a", "Oświadczenie A"),
                                   ("statement_b", "Oświadczenie B"))
    ])
    env.enter()
    client = env.app.test_client()
    row = env.row()
    url = f"/do-podpisania?submission_id={env.public_id}&token={row['access_token']}"
    csrf = client.get(url).text.split('name="csrf_token" value="')[1].split('"')[0]
    for document_id in ("statement_a", "statement_b"):
        response = client.post(
            f"/document-steps/{row['form_slug']}/{env.public_id}/paper/generate/{document_id}",
            data={"csrf_token": csrf, "access_token": row["access_token"]})
        assert response.status_code == 302
    html = client.get(url).text
    css_root = Path(__file__).resolve().parents[1] / "static" / "css"
    with playwright_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except playwright_api.Error as exception:
            pytest.skip(f"Brak przeglądarki Playwright: {exception}")
        page = browser.new_page()
        page.route("**/*", lambda route: route.abort())
        page.set_content(html)
        for name in ("style.css", "documents_to_sign.css"):
            page.add_style_tag(content=(css_root / name).read_text(encoding="utf-8"))
        for theme in ("light", "dark"):
            page.locator("html").evaluate("(element, value) => element.setAttribute('data-theme', value)", theme)
            for width in (390, 1280):
                page.set_viewport_size({"width": width, "height": 900})
                cards = page.locator(".document-stage-file")
                assert cards.count() == 2
                for card in cards.all():
                    layout = card.evaluate("""element => {
                        const style = getComputedStyle(element);
                        const bounds = element.getBoundingClientRect();
                        const download = element.querySelector('.document-stage-file__download');
                        const upload = element.querySelector('.document-stage-file__upload');
                        return {border: style.borderTopWidth, radius: style.borderTopLeftRadius,
                            padding: style.paddingLeft, background: style.backgroundColor,
                            left: bounds.left, right: bounds.right,
                            downloadBottom: download?.getBoundingClientRect().bottom,
                            uploadTop: upload?.getBoundingClientRect().top,
                            overflow: element.scrollWidth > element.clientWidth};
                    }""")
                    assert layout["border"] != "0px" and layout["radius"] != "0px"
                    assert layout["padding"] != "0px" and layout["background"] != "rgba(0, 0, 0, 0)"
                    assert layout["left"] >= 0 and layout["right"] <= width
                    assert layout["downloadBottom"] <= layout["uploadTop"]
                    assert not layout["overflow"]
                page.screenshot(path=str(tmp_path / f"document-cards-{theme}-{width}.png"), full_page=True)
        browser.close()


def test_custom_document_office_signature_waits_for_office(composite_env):
    # Given: a participant stage whose required custom document needs two signatures.
    env = composite_env()
    configure_participant_document_stage(env, [{"id": "office_statement", "label": "Oświadczenie",
        "kind": "generated_pdf", "template_html": "<p>Dokument</p>", "workflow_step": "paper",
        "required": True, "signature_required": True,
        "lifecycle": {"generate": True, "upload_required": True,
            "participant_signature": True, "office_signature": True}}])
    env.enter()
    env.document.generate_group_document(env.row(), env.definition, "paper", "office_statement")
    # When: the participant uploads a verified signed revision.
    participant = env.document.upload(env.row(), env.definition, "paper", env.file(),
        document_id="office_statement")
    # Expected: the document and stage wait for the office signature.
    assert participant["is_valid"] and not participant["completed"]
    assert not env.step_state()["documents"]["office_statement"]["completed"]
    assert env.row()["workflow_step"] == "paper"
    env.verification["signature_count"] = 2
    office_file = FileStorage(stream=BytesIO(env.pdf + b"\n% participant signature\n\n% office signature"),
        filename="office.pdf", content_type="application/pdf")
    office = env.document.upload(env.row(), env.definition, "paper", office_file,
        signer="office", document_id="office_statement")
    assert office["is_valid"] and office["completed"]
    assert env.row()["workflow_step"] == "review"


@pytest.mark.parametrize("generic", [False, True])
def test_custom_document_group_required_and_optional_uploads(composite_env, generic):
    # Given: two required participant files and one optional file on one step.
    env = composite_env("employment_certificate")
    env.definition["documents"] = [
        {"id": "employment_certificate", "label": "Zaświadczenie", "kind": "uploaded_file",
         "required": True, "signature_required": False, "workflow_step": "paper"},
        {"id": "participant_statement", "label": "Oświadczenie", "kind": "uploaded_file",
         "required": True, "signature_required": False, "workflow_step": "paper"},
        {"id": "optional_attachment", "label": "Załącznik", "kind": "uploaded_file",
         "required": False, "signature_required": False, "workflow_step": "paper"},
    ]
    if generic:
        configure_generic_document_stage(env, env.definition["documents"])
    env.enter()
    assert env.row()["workflow_step"] == "paper"
    view = env.document.view(env.row(), env.definition)
    assert view["required_count"] == 2
    assert {file["document_id"] for file in view["files"]} == {
        "employment_certificate", "participant_statement", "optional_attachment"}
    def upload(document_id):
        return env.document.upload(env.row(), env.definition, "paper",
            FileStorage(stream=BytesIO(env.pdf), filename=f"{document_id}.pdf", content_type="application/pdf"),
            document_id=document_id)
    # When: the optional document and first required document are uploaded.
    assert upload("optional_attachment")["is_valid"]
    assert upload("employment_certificate")["is_valid"]
    # Expected: the workflow still waits for the second required document.
    assert env.row()["workflow_step"] == "paper"
    assert env.document.view(env.row(), env.definition)["ready_count"] == 1
    with pytest.raises(ValueError, match="tym etapie"):
        upload("foreign_document")
    assert upload("participant_statement")["completed"]
    assert env.row()["workflow_step"] == "review"
    files = env.services.submission_document_service.list_documents(env.public_id)
    assert {file["document_id"] for file in files} == {
        "employment_certificate", "participant_statement", "optional_attachment"}
    assert all(file["workflow_step_at_upload"] == "paper" for file in files)


def test_admin_custom_document_config_keeps_builder_and_step(composite_env):
    from services.admin_form_service import build_form_definition_from_admin_form
    # Given: an existing generic builder document in the draft definition.
    env = composite_env()
    env.definition["documents"].append({"id": "employment_certificate", "label": "Stare",
        "kind": "generated_pdf", "workflow_step": "paper", "template_source": "builder",
        "builder_document": {"version": 1, "document_type": "document", "blocks": [
            {"type": "paragraph", "runs": [{"text": "{{ participant_name }}"}]}]}})
    submitted = {"documents_config_json": json.dumps([{"id": "employment_certificate",
        "label": "Zaświadczenie", "participant_label": "Dokument od pracodawcy",
        "description": "Prześlij dokument", "kind": "generated_pdf", "template_source": "builder",
        "workflow_step": "paper", "required": True, "signature_required": False,
        "enabled": True, "order": 2}])}
    # When: the administrator saves the document metadata.
    definition = build_form_definition_from_admin_form(env.definition, submitted)
    document = next(item for item in definition["documents"] if item["id"] == "employment_certificate")
    # Expected: the selected step and independent flags are versioned; an
    # already edited DocumentBuilder document is preserved.
    assert document["workflow_step"] == "paper"
    assert document["required"] is True and document["signature_required"] is False
    assert document["label"] == "Zaświadczenie"
    assert document["participant_label"] == "Dokument od pracodawcy"
    assert document["builder_document"]["blocks"][0]["type"] == "paragraph"


def test_admin_declaration_document_name_uses_existing_versioned_config(composite_env):
    from services.admin_form_service import build_form_definition_from_admin_form

    env = composite_env()
    named = build_form_definition_from_admin_form(env.definition, {
        "requires_declaration": "on",
        "declaration_document_name": "Oświadczenie uczestnika",
    })
    declaration = next(document for document in named["documents"] if document["id"] == "declaration")
    assert declaration["participant_label"] == "Oświadczenie uczestnika"
    assert declaration["id"] == "declaration"

    renamed = build_form_definition_from_admin_form(named, {
        "requires_declaration": "on",
        "declaration_document_name": "Oświadczenie o spełnianiu kryteriów udziału",
    })
    declaration = next(document for document in renamed["documents"] if document["id"] == "declaration")
    assert declaration["participant_label"] == "Oświadczenie o spełnianiu kryteriów udziału"
    unnamed = build_form_definition_from_admin_form(renamed, {
        "requires_declaration": "on", "declaration_document_name": "",
    })
    declaration = next(document for document in unnamed["documents"] if document["id"] == "declaration")
    assert declaration["participant_label"] == ""
    assert declaration["label"] == "Dokument testowy"


def test_admin_document_name_fields_are_shown_in_document_settings(admin_app):
    create_user(admin_app)
    form_id = create_form(admin_app, slug=f"named-document-{uuid4().hex[:8]}", definition_json={
        "title": "Oświadczenia", "fields": [],
        "documents": [
            {"id": "declaration", "label": "Deklaracja", "participant_label": "Oświadczenie uczestnika"},
            {"id": "statement", "label": "Oświadczenia", "participant_label": "Oświadczenie o regulaminie"},
        ],
        "workflow": {"requires_declaration": True},
    })
    client = admin_app.test_client()
    login(client)
    page = client.get(f"/admin/forms/{form_id}/edit?tab=documents")
    assert page.status_code == 200
    assert '<span>Nazwa dokumentu</span><input data-custom-field="participant_label"' in page.text
    assert 'name="declaration_document_name" value="Oświadczenie uczestnika"' in page.text
    config_json = page.text.split('<script type="application/json" data-custom-documents-initial>', 1)[1].split('</script>', 1)[0]
    configured = json.loads(config_json)
    assert next(item for item in configured if item["id"] == "statement")["participant_label"] == "Oświadczenie o regulaminie"


def test_admin_assigns_generated_upload_document_to_participant_stage(composite_env):
    from services.admin_form_service import build_form_definition_from_admin_form
    # Given: a participant action with no primary document_id.
    env = composite_env()
    configure_participant_document_stage(env, [])
    payload = {"documents_config_json": json.dumps([{"id": "employment_certificate",
        "label": "Zaświadczenie", "participant_label": "Zaświadczenie uczestnika",
        "workflow_step": "paper", "required": True, "signature_required": True,
        "generate": True, "upload_required": True, "office_signature": True,
        "template_source": "builder", "enabled": True, "order": 1}])}
    # When: the admin saves independent lifecycle controls.
    definition = build_form_definition_from_admin_form(env.definition, payload)
    document = definition["documents"][0]
    # Expected: one versioned config supports generate -> download -> signed
    # upload -> office countersignature on the selected participant stage.
    assert document["workflow_step"] == "paper"
    assert document["kind"] == "generated_pdf"
    assert document["lifecycle"]["generate"] and document["lifecycle"]["upload_required"]
    assert document["lifecycle"]["participant_signature"] and document["lifecycle"]["office_signature"]


def test_admin_custom_document_html_template_is_saved(composite_env):
    from services.admin_form_service import build_form_definition_from_admin_form
    # Given: an uploaded source is changed to a generated HTML document.
    env = composite_env()
    submitted = {"documents_config_json": json.dumps([{"id": "employment_certificate",
        "label": "Zaświadczenie", "kind": "generated_pdf", "template_source": "html",
        "template_html": "<p>{{ participant_name }}</p>", "workflow_step": "paper",
        "required": True, "signature_required": False, "enabled": True}])}
    # When: the administrator saves the document configuration.
    definition = build_form_definition_from_admin_form(env.definition, submitted)
    # Expected: the HTML source is stored with the versioned document.
    assert definition["documents"][-1]["template_html"] == "<p>{{ participant_name }}</p>"


def test_custom_document_bulk_mapping_and_wrong_document_rejected(composite_env):
    # Given: the effective FormVersion has two required participant uploads.
    env = composite_env("employment_certificate")
    env.definition["documents"] = [
        {"id": document_id, "label": document_id, "kind": "uploaded_file", "required": True,
         "signature_required": False, "workflow_step": "paper"}
        for document_id in ("employment_certificate", "participant_statement")]
    with env.repo.session_factory() as db:
        db.get(FormVersion, env.row()["form_version_id"]).definition_json = deepcopy(env.definition)
        db.commit()
    env.enter()
    env.app.config.update(PUBLIC_CSRF_ENABLED=True)
    client = env.app.test_client()
    row = env.row()
    page = client.get(f"/do-podpisania?submission_id={env.public_id}&token={row['access_token']}")
    assert page.status_code == 200
    csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    url = f"/document-steps/{row['form_slug']}/{env.public_id}/paper/bulk-upload?token={row['access_token']}"
    def payload(ids):
        return {"csrf_token": csrf, "access_token": row["access_token"], "document_ids": ids,
                "document_files": [(BytesIO(env.pdf), "first.pdf"), (BytesIO(env.pdf), "second.pdf")]}
    # When: a document_id outside this version is submitted, mapping is denied.
    assert client.post(url, data=payload(["foreign", "participant_statement"]),
                       content_type="multipart/form-data").status_code == 404
    assert not env.services.submission_document_service.list_documents(env.public_id)
    partial = client.post(url, data={"csrf_token": csrf, "access_token": row["access_token"],
        "document_ids": ["employment_certificate", "participant_statement"],
        "document_files": [(BytesIO(env.pdf), "first.pdf"), (BytesIO(b"not a document"), "second.exe")]},
        content_type="multipart/form-data", follow_redirects=True)
    assert partial.status_code == 200 and "second.exe" in partial.text
    assert env.row()["workflow_step"] == "paper"
    assert {file["document_id"] for file in env.services.submission_document_service.list_documents(env.public_id)} == {"employment_certificate"}
    # When: the missing document is retried, each SubmissionFile retains its
    # explicit ID and the workflow advances only after both are accepted.
    result = client.post(url, data=payload(["employment_certificate", "participant_statement"]),
                         content_type="multipart/form-data")
    assert result.status_code == 302
    assert env.row()["workflow_step"] == "review"
    files = env.services.submission_document_service.list_documents(env.public_id)
    assert {file["document_id"] for file in files} == {"employment_certificate", "participant_statement"}


def test_admin_custom_document_builder_uses_shared_editor(admin_app):
    # Given: a custom generated document in an editable form.
    form_id = create_form(admin_app, slug=f"custom-builder-{uuid4().hex[:8]}", definition_json={
        "title": "Test", "fields": [], "documents": [{"id": "employment_certificate",
            "label": "Zaświadczenie", "kind": "generated_pdf", "workflow_step": "paper",
            "template_source": "builder", "builder_document": {
                "version": 1, "document_type": "document", "blocks": [{"type": "paragraph",
                    "runs": [{"text": "{{ participant_name }}"}]}]}}]})
    create_user(admin_app)
    client = admin_app.test_client()
    login(client)
    editor = client.get(f"/admin/forms/{form_id}/edit?tab=documents")
    assert editor.status_code == 200 and 'data-custom-documents' in editor.text
    builder_url = f"/admin/forms/{form_id}/documents/employment_certificate/builder"
    page = client.get(builder_url)
    assert page.status_code == 200
    assert 'data-document-type="document"' in page.text
    assert 'data-document-id="employment_certificate"' in page.text
    csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    # When: the shared builder saves an edited paragraph.
    document = {"version": 1, "document_type": "document", "blocks": [{"type": "paragraph",
        "runs": [{"text": "Zaświadczenie dla {{ participant_name }}"}]}]}
    saved = client.post(builder_url, data={"csrf_token": csrf, "builder_json": json.dumps(document), "action": "activate"})
    assert saved.status_code == 200 and saved.json["ok"]
    with admin_app.extensions["services"].submission_repository.session_factory() as db:
        from models import Form
        entry = db.get(Form, form_id).definition_json["documents"][0]
        assert entry["builder_status"] == "active"
        assert entry["builder_document"]["document_type"] == "document"


def test_version_gap_upload_keeps_current_workflow_step(composite_env):
    # Given: publication marked a new required file on an already visited step.
    env = composite_env()
    env.definition["documents"].append({"id": "employment_certificate", "label": "Zaświadczenie",
        "kind": "uploaded_file", "required": True, "signature_required": False, "workflow_step": "paper"})
    with env.repo.session_factory() as db:
        db.get(FormVersion, env.row()["form_version_id"]).definition_json = deepcopy(env.definition)
        db.commit()
    env.repo.update(env.public_id, {"workflow_step": "review", "workflow_stage": "review",
        "data_json": {"_form_version_upgrade": {"missing_fields": [], "invalid_training_ids": [],
            "missing_documents": [{"step_id": "paper", "document_id": "employment_certificate",
                "label": "Zaświadczenie", "kind": "uploaded_file", "signature_required": False}]}}})
    env.app.config.update(PUBLIC_CSRF_ENABLED=True)
    client = env.app.test_client()
    row = env.row()
    page = client.get(f"/do-podpisania?submission_id={env.public_id}&token={row['access_token']}")
    assert page.status_code == 200 and "Zaświadczenie" in page.text
    csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    url = f"/version-documents/{row['form_slug']}/{env.public_id}/employment_certificate/upload?token={row['access_token']}"
    accepted = client.post(url, data={"csrf_token": csrf, "access_token": row["access_token"],
        "document_pdf": (BytesIO(env.pdf), "certificate.pdf")}, content_type="multipart/form-data")
    assert accepted.status_code == 302
    assert env.row()["workflow_step"] == "review"
    assert env.row()["data_json"]["_form_version_upgrade"]["missing_documents"] == []
    file = next(file for file in env.services.submission_document_service.list_documents(env.public_id)
                if file["document_id"] == "employment_certificate")
    assert file["workflow_step_at_upload"] == "paper" and file["status"] == "uploaded"


def test_version_gap_generated_document_requires_signature(composite_env):
    # Given: a new required generated PDF on a completed step.
    env = composite_env()
    env.definition["documents"].append({"id": "participant_statement", "label": "Oświadczenie",
        "kind": "generated_pdf", "required": True, "signature_required": True,
        "workflow_step": "paper", "template_html": "<p>{{ participant_name }}</p>"})
    with env.repo.session_factory() as db:
        db.get(FormVersion, env.row()["form_version_id"]).definition_json = deepcopy(env.definition)
        db.commit()
    env.repo.update(env.public_id, {"workflow_step": "review", "workflow_stage": "review",
        "data_json": {"_form_version_upgrade": {"missing_fields": [], "invalid_training_ids": [],
            "missing_documents": [{"step_id": "paper", "document_id": "participant_statement",
                "label": "Oświadczenie", "kind": "generated_pdf", "signature_required": True}]}}})
    row = env.row()
    env.app.config.update(PUBLIC_CSRF_ENABLED=True)
    client = env.app.test_client()
    page_url = f"/do-podpisania?submission_id={env.public_id}&token={row['access_token']}"
    page = client.get(page_url)
    csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    generate_url = f"/version-documents/{row['form_slug']}/{env.public_id}/participant_statement/generate?token={row['access_token']}"
    assert client.post(generate_url, data={"csrf_token": csrf, "access_token": row["access_token"]}).status_code == 302
    generated = next(file for file in env.services.submission_document_service.list_documents(env.public_id)
                     if file["document_id"] == "participant_statement" and file["status"] == "generated")
    assert env.row()["data_json"]["_form_version_upgrade"]["missing_documents"]
    assert client.get(f"/version-documents/{row['form_slug']}/{env.public_id}/participant_statement/download/{generated['filename']}?token={row['access_token']}").status_code == 200
    upload_url = f"/version-documents/{row['form_slug']}/{env.public_id}/participant_statement/upload?token={row['access_token']}"
    uploaded = client.post(upload_url, data={"csrf_token": csrf, "access_token": row["access_token"],
        "document_pdf": (BytesIO(env.pdf + b"\n% signed revision"), "signed.pdf")}, content_type="multipart/form-data")
    assert uploaded.status_code == 302
    assert env.row()["workflow_step"] == "review"
    assert env.row()["data_json"]["_form_version_upgrade"]["missing_documents"] == []


def test_custom_document_requires_verified_signature(composite_env):
    # Given: a generated custom PDF whose participant signature is required.
    env = composite_env("participant_statement")
    env.definition["documents"][0].update(required=True, signature_required=True, workflow_step="paper")
    env.enter()
    assert env.row()["workflow_step"] == "paper"
    assert not env.state().get("completed")
    assert env.document.view(env.row(), env.definition)["files"][0]["status"] == "Oczekuje na podpis"
    # When: the unsigned original is uploaded, signature verification rejects it.
    env.verification.update(is_signed=False, validation_status="UNSIGNED", signature_count=0)
    result = env.document.upload(env.row(), env.definition, "paper",
        FileStorage(stream=BytesIO(env.pdf), filename="unsigned.pdf", content_type="application/pdf"),
        document_id="participant_statement")
    assert not result["is_valid"]
    assert env.row()["workflow_step"] == "paper"
    # When: a valid signed revision is uploaded, the metadata becomes signed
    # and only then can the workflow leave the document step.
    env.verification.update(is_signed=True, validation_status="VALID", signature_count=1)
    accepted = env.document.upload(env.row(), env.definition, "paper", env.file(),
        document_id="participant_statement")
    assert accepted["is_valid"] and accepted["completed"]
    assert env.row()["workflow_step"] == "review"
    signed_files = [file for file in env.services.submission_document_service.list_documents(env.public_id)
                    if file["document_id"] == "participant_statement" and file["signed"]]
    assert len(signed_files) == 1 and signed_files[0]["status"] == "signed"
    assert env.document.upload(env.row(), env.definition, "paper", env.file(),
        document_id="participant_statement")["completed"]


def test_document_fields_collect_save_generate_refresh(composite_env):
    env = composite_env()
    configure_document_fields(env)
    env.repo.update(env.public_id, {'data_json': {'initial_value': 'preserved'}})
    env.enter()
    env.app.config.update(PUBLIC_CSRF_ENABLED=True)
    assert env.state()['substate'] == 'collect_data'
    assert env.generated == []
    client = env.app.test_client()
    row = env.row()
    page_url = f"/do-podpisania?submission_id={env.public_id}&token={row['access_token']}"
    fields_url = f"/document-steps/{row['form_slug']}/{env.public_id}/paper/fields?token={row['access_token']}"
    page = client.get(page_url)
    assert page.status_code == 200
    assert 'name="deklaracja_18_lat"' in page.text
    assert 'name="wybor_szkolen"' not in page.text
    assert 'Wybierz szkolenia' not in page.text
    assert '/paper/download/' not in page.text and 'data-document-upload' not in page.text
    csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    assert client.post(fields_url, data={}).status_code == 400
    assert client.post(fields_url.split('?')[0], data={'csrf_token': csrf}).status_code in {403, 404}
    result = client.post(fields_url, data={'csrf_token': csrf})
    assert result.status_code == 400
    assert env.generated == []
    data = {'csrf_token': csrf, 'deklaracja_18_lat': 'Tak', 'declaration_note': 'Zapisana wartość'}
    assert client.post(fields_url, data={**data, 'document_action': 'save'}).status_code == 302
    assert env.state()['substate'] == 'collect_data' and not env.generated
    assert env.row()['data_json']['initial_value'] == 'preserved'
    from models import SubmissionTraining
    with env.repo.session_factory() as db:
        training = db.execute(select(SubmissionTraining).where(
            SubmissionTraining.submission_id == env.row()['id'])).scalar_one_or_none()
        assert training is None
    assert env.row()['workflow_step'] == env.row()['workflow_stage'] == 'paper'
    assert 'Zapisana wartość' in client.get(page_url).text
    assert client.post(fields_url, data=data).status_code == 302
    assert env.state()['substate'] == 'ready'
    assert len(env.generated) == 1
    assert env.generated[0]['context']['deklaracja_18_lat'] == 'Tak'
    assert env.generated[0]['context']['declaration_note'] == 'Zapisana wartość'
    assert not env.generated[0]['context']['selected_trainings']
    assert '/paper/download/' in client.get(page_url).text
    snapshot = deepcopy(env.row()['data_json'])
    assert client.post(fields_url, data={**data, 'declaration_note': 'Zmieniona'}).status_code == 303
    assert env.row()['data_json'] == snapshot
    assert len(env.generated) == 1
    assert len(env.definition['workflow']['steps']) == 4
    assert env.definition['fields'] == []


def test_document_fields_replace_premature_pdf_and_fail_generation(composite_env, monkeypatch):
    from werkzeug.datastructures import MultiDict
    env = composite_env()
    env.enter()
    old_file = env.state()['generated_filename']
    configure_document_fields(env)
    assert env.document.view(env.row(), env.definition)['substate'] == 'collect_data'
    with pytest.raises(ValueError):
        env.document.download(env.row(), env.definition, 'paper', old_file)
    data = MultiDict({'deklaracja_18_lat': 'Tak', 'wybor_szkolen': 'course-a', 'declaration_note': 'Nowa treść'})
    original = env.services.document_service.pdf_render_service.render_document_pdf_bytes
    def fail(**kwargs):
        raise RuntimeError('Test renderer failure')
    monkeypatch.setattr(env.services.document_service.pdf_render_service, 'render_document_pdf_bytes', fail)
    with pytest.raises(RuntimeError):
        env.document.save_fields(env.row(), env.definition, 'paper', data)
    assert env.state()['substate'] == 'failed'
    assert env.document.view(env.row(), env.definition)['form_definition']
    assert not env.document.view(env.row(), env.definition)['files']
    assert env.row()['data_json']['declaration_note'] == 'Nowa treść'
    monkeypatch.setattr(env.services.document_service.pdf_render_service, 'render_document_pdf_bytes', original)
    data['declaration_note'] = 'Poprawiona treść'
    assert env.document.save_fields(env.row(), env.definition, 'paper', data).success
    assert env.state()['substate'] == 'ready'
    assert env.state()['generated_filename'] != old_file
    old = next(f for f in env.services.submission_document_service.list_documents(env.public_id) if f['filename'] == old_file)
    assert old['status'] == 'superseded'
    with pytest.raises(ValueError):
        env.document.download(env.row(), env.definition, 'paper', old_file)


@pytest.mark.parametrize('provider', ['mszafir', 'profil_zaufany'])
def test_real_document_signature_raw_bytes_idempotency_transition(composite_env, signed_pdf_factory, monkeypatch, caplog, provider):
    from hashlib import sha256
    from signature_verifier import verify_signed_pdf
    env = composite_env()
    env.enter()
    signed = signed_pdf_factory(env.pdf, provider)
    digests = []
    def verify(path):
        digests.append(sha256(path.read_bytes()).hexdigest())
        return verify_signed_pdf(path)
    monkeypatch.setattr(env.services.document_signing_service, 'verifier', verify)
    records = []
    original_record = env.services.submission_document_service.record_document_metadata
    def record(**kwargs):
        records.append(kwargs)
        return original_record(**kwargs)
    monkeypatch.setattr(env.services.submission_document_service, 'record_document_metadata', record)
    def upload():
        return env.document.upload(env.row(), env.definition, 'paper', FileStorage(stream=BytesIO(signed), filename='signed.pdf', content_type='application/pdf'))
    with caplog.at_level('INFO'):
        assert upload()['result'] == 'VALID_SIGNATURE'
    assert len(records) == 1
    assert digests == [sha256(signed).hexdigest()]
    assert records[0]['file_bytes'] == signed
    verification = records[0]['signature_validation_result']
    assert verification['upload_sha256'] == verification['storage_sha256'] == verification['verifier_sha256'] == digests[0]
    assert env.state()['completed'] and env.row()['workflow_stage'] == 'review'
    assert upload()['completed']
    assert len(records) == len(digests) == 1
    files = env.services.submission_document_service.list_documents(env.public_id)
    assert len(files) == 2
    signed_file = next(f for f in files if f['signed'])
    assert env.document.download(env.row(), env.definition, 'paper', signed_file['filename']) == signed
    assert 'result=VALID_SIGNATURE' in caplog.text
    assert 'TEST COPE SZAFIR' not in caplog.text
    assert 'TEST Profil Zaufany' not in caplog.text
    expected_backend = 'trusted_profile_minimal_cms' if provider == 'profil_zaufany' else 'pyhanko_pdf_cms'
    for expected in ['cryptographic_valid=True', 'document_integrity_valid=True', 'certificate_chain_valid=True',
                     'timestamp_valid=None', f'detected_signature_type={provider}', f'verifier_backend={expected_backend}', 'provider_allowed=True']:
        assert expected in caplog.text
    with env.repo.session_factory() as db:
        events = db.execute(select(SubmissionWorkflowEvent).where(SubmissionWorkflowEvent.public_submission_id == env.public_id)).scalars().all()
        assert sum(e.source == 'DOCUMENT_STEP_COMPLETED' for e in events) == 1
        assert sum(e.previous_step == 'paper' and e.new_step == 'review' for e in events) == 1


@pytest.mark.parametrize('failure', ['corruption', 'read_error'])
def test_signed_upload_storage_integrity_failure(composite_env, monkeypatch, failure):
    env = composite_env()
    env.enter()
    calls = []
    monkeypatch.setattr(env.services.document_signing_service, 'verifier', lambda path: calls.append(path))
    def read(*args, **kwargs):
        if failure == 'read_error':
            raise TimeoutError('Test storage unavailable')
        return b'%PDF-1.4\ncorrupted stored bytes'
    monkeypatch.setattr(env.services.document_signing_service.document_storage_service, 'read_document_bytes', read)
    result = env.document.upload(env.row(), env.definition, 'paper', env.file())
    assert result['result'] == 'VERIFICATION_ERROR'
    assert not calls
    assert env.row()['workflow_stage'] == 'paper'
    files = env.services.submission_document_service.list_documents(env.public_id)
    rejected = [file for file in files if file['status'] == 'rejected']
    assert len(rejected) == 1
    verification = rejected[0]['signature_validation_result']
    assert verification['reason_code'] == ('UPLOAD_STORAGE_HASH_MISMATCH' if failure == 'corruption' else 'STORAGE_READ_ERROR')
    assert verification['verifier_sha256'] is None


def test_document_fields_generation_contains_saved_data(composite_env, monkeypatch):
    from werkzeug.datastructures import MultiDict
    from pypdf import PdfReader
    from services.documents.pdf_render_service import PdfRenderService
    from pdf_generator import generate_pdf_from_html
    env = composite_env()
    configure_document_fields(env)
    env.enter()
    monkeypatch.setattr(env.services.document_service, 'pdf_render_service', PdfRenderService(html_renderer=generate_pdf_from_html))
    assert env.document.save_fields(env.row(), env.definition, 'paper', MultiDict({
        'deklaracja_18_lat': 'Tak', 'wybor_szkolen': 'course-a', 'declaration_note': 'Saved document values'})).success
    filename = env.state()['generated_filename']
    pdf = env.document.download(env.row(), env.definition, 'paper', filename)
    rendered = ''.join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)
    assert 'Wiek: Tak' in rendered and 'Saved document values' in rendered


@pytest.mark.parametrize('failure', ['unsigned', 'timeout', 'storage', 'storage_read', 'metadata', 'unsupported'])
def test_document_signature_failure_messages(composite_env, monkeypatch, failure):
    from signature_verifier import verify_signed_pdf
    env = composite_env()
    env.enter()
    if failure == 'unsigned':
        monkeypatch.setattr(env.services.document_signing_service, 'verifier', verify_signed_pdf)
    elif failure == 'unsupported':
        env.definition['documents'][0]['allowed_signatures'] = ['profil_zaufany']
    elif failure == 'metadata':
        monkeypatch.setattr(env.services.submission_document_service, 'record_document_metadata', lambda **kwargs: False)
    else:
        def fail(*args, **kwargs):
            raise TimeoutError('Unavailable')
        if failure == 'timeout':
            monkeypatch.setattr(env.services.document_signing_service, 'verifier', fail)
        elif failure == 'storage':
            monkeypatch.setattr(env.services.document_service.document_storage_service, 'save_pdf', fail)
        else:
            monkeypatch.setattr(env.services.document_service, 'read_document_bytes_for_download', fail)
    result = env.document.upload(env.row(), env.definition, 'paper', env.file())
    expected = {'unsigned': 'NO_SIGNATURE', 'unsupported': 'SIGNATURE_TYPE_NOT_ALLOWED'}.get(failure, 'VERIFICATION_ERROR')
    assert result['result'] == expected
    assert env.row()['workflow_stage'] == 'paper'
    assert env.state()['substate'] == ('verification_failed' if failure in {'unsigned', 'unsupported'} else 'verification_error')
    page = env.app.test_client().get(f"/do-podpisania?submission_id={env.public_id}&token={env.row()['access_token']}")
    assert result['message'] in page.text
    assert env.document.view(env.row(), env.definition)['files'][0]['can_upload']


def test_composite_state_machine(composite_env):
    env = composite_env()
    env.enter()
    assert len(env.definition['workflow']['steps']) == 4
    assert env.row()['workflow_stage'] == 'paper'
    assert env.state()['substate'] == 'ready'
    assert len(env.generated) == 1
    env.services.workflow_service.run_automatic_steps(env.row(), env.definition)
    assert len(env.generated) == 1
    files = env.services.document_service.submission_document_service.list_documents(env.public_id)
    assert len(files) == 1 and files[0]['workflow_step_at_upload'] == 'paper'
    assert env.repo.claim_document_operation(env.public_id, 'paper', 'first')
    assert not env.repo.claim_document_operation(env.public_id, 'paper', 'second')
    env.repo.release_document_operation(env.public_id, 'first')


@pytest.mark.parametrize('outcome', ['INVALID', 'INDETERMINATE', 'UNSIGNED'])
def test_composite_transition_requires_verified_signature(composite_env, outcome):
    env = composite_env()
    env.enter()
    with pytest.raises(WorkflowTransitionError):
        env.services.workflow_service.advance_after_action(env.row(), env.definition)
    with pytest.raises(WorkflowTransitionError):
        env.services.workflow_service.transition_to(env.public_id, 'review')
    env.verification['validation_status'] = outcome
    assert not env.document.upload(env.row(), env.definition, 'paper', env.file())['is_valid']
    assert env.row()['workflow_stage'] == 'paper'
    assert env.state()['substate'] == ('verification_error' if outcome == 'INDETERMINATE' else 'verification_failed')
    assert env.document.view(env.row(), env.definition)['files'][0]['can_upload']
    env.verification['validation_status'] = 'VALID'
    assert env.document.upload(env.row(), env.definition, 'paper', env.file())['completed']
    assert env.row()['workflow_stage'] == 'review'
    assert env.document.upload(env.row(), env.definition, 'paper', env.file())['completed']
    with env.repo.session_factory() as db:
        events = db.execute(select(SubmissionWorkflowEvent).where(SubmissionWorkflowEvent.public_submission_id == env.public_id)).scalars().all()
        assert sum(e.source == 'DOCUMENT_STEP_COMPLETED' for e in events) == 1
        assert sum(e.previous_step == 'paper' and e.new_step == 'review' for e in events) == 1
        assert {'DOCUMENT_GROUP_READY', 'SIGNATURE_VERIFICATION_FAILED',
                'DOCUMENT_STEP_COMPLETED'} <= {
                    e.source for e in events}


def test_composite_instructions(composite_env):
    env = composite_env()
    env.enter()
    workflow = env.definition['workflow']
    workflow['steps'][1]['document_instructions']['upload'] = {'description': 'Wgraj podpisany plik.'}
    for substate, expected in [('ready', 'Pobierz swój plik.'), ('upload', 'Wgraj podpisany plik.'), ('verification_failed', 'Podpisz ponownie.')]:
        view = build_process_instruction_view('DECLARATION_WAITING_FOR_SIGNATURE', workflow=workflow, step_id='paper', document_substate=substate)
        assert expected in view['instruction']['current_stage_description']
        assert len(view['instruction']['stages']) == 4
    status = build_readonly_submission_status(env.row(), form_config=env.definition)
    assert list(status) == ['status_label']
    assert 'gotowy do pobrania' in status['status_label']
    assert validate_document_step(workflow['steps'][1], env.definition) == []
    bad = deepcopy(workflow['steps'][1])
    bad['document_instructions']['unknown'] = {}
    assert validate_document_step(bad, env.definition)


def test_composite_declaration_download_upload_access(composite_env):
    env = composite_env()
    env.enter()
    row = env.row()
    client = env.app.test_client()
    env.app.config['WTF_CSRF_ENABLED'] = True
    filename = env.state()['generated_filename']
    base = f"/document-steps/{row['form_slug']}/{env.public_id}/paper"
    assert client.get(f'{base}/download/{filename}').status_code == 404
    assert client.get(f'{base}/download/{filename}?token=wrong').status_code == 404
    other = composite_env()
    assert client.get(f"{base}/download/{filename}?token={other.row()['access_token']}").status_code == 404
    download = client.get(f"{base}/download/{filename}?token={row['access_token']}")
    assert download.status_code == 200 and download.data == env.pdf
    assert env.state()['substate'] == 'awaiting_signature'
    page = client.get(f"/do-podpisania?submission_id={env.public_id}&token={row['access_token']}")
    assert page.status_code == 200 and b'data-composite-document' in page.data
    csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    env.app.config['PUBLIC_CSRF_ENABLED'] = True
    upload = {'access_token': row['access_token'], 'document_pdf': (BytesIO(env.pdf + b'\n% signed\n'), 'signed.pdf')}
    assert client.post(f'{base}/upload', data=upload).status_code == 400
    response = client.post(f'{base}/upload', data={'access_token': row['access_token'], 'csrf_token': csrf,
        'document_pdf': (BytesIO(env.pdf + b'\n% signed\n'), 'signed.pdf')})
    assert response.status_code == 302
    assert env.row()['workflow_stage'] == 'review'
    assert env.row()['data_json']['_signed_declaration_snapshot']['form_version_id'] == row['form_version_id']


@pytest.mark.parametrize('mode', ['participant', 'office', 'both', 'unsigned', 'upload_only'])
def test_composite_agreement_and_document_capabilities(composite_env, mode):
    env = composite_env('agreement', generate=mode != 'upload_only', participant_signature=mode in {'participant', 'both'},
        upload_required=mode in {'participant', 'both', 'upload_only'}, office_signature=mode in {'office', 'both'})
    env.enter()
    if mode == 'unsigned':
        assert env.row()['workflow_stage'] == 'review'
        return
    if mode != 'office':
        result = env.document.upload(env.row(), env.definition, 'paper', env.file())
        assert result['is_valid']
    if mode in {'office', 'both'}:
        assert env.row()['workflow_stage'] == 'paper'
        env.verification['signature_count'] = 2 if mode == 'both' else 1
        result = env.document.upload(env.row(), env.definition, 'paper', env.file(b'\n% participant signature\n\n% office signature\n'), signer='office')
        assert result['is_valid']
    assert env.row()['workflow_stage'] == 'review'


def test_composite_generic_builder_and_historical_workflow(composite_env):
    env = composite_env('statement')
    env.enter()
    assert env.document.upload(env.row(), env.definition, 'paper', env.file())['completed']
    assert any(f['document_type'] == 'signed_statement' for f in env.services.document_service.submission_document_service.list_documents(env.public_id))
    html = env.services.document_service.resolve_document_template({'id': 'statement', 'template_source': 'builder',
        'builder_document': {'version': 1, 'blocks': [{'id': 'text', 'type': 'paragraph', 'content': 'Oświadczenie'}]}})
    assert 'Oświadczenie' in html
    historical = deepcopy(env.definition)
    historical['workflow']['steps'][1].pop('document_lifecycle')
    historical['workflow']['steps'][1]['action'] = 'await_signature'
    assert env.services.document_service.composite_step({'workflow_stage': 'paper'}, historical) is None
    with env.repo.session_factory() as db:
        version = db.get(FormVersion, env.row()['form_version_id'])
        assert version.definition_json == env.definition


def test_composite_verifier_failure_and_source_binding(composite_env, monkeypatch):
    env = composite_env()
    env.enter()
    verifier = env.services.document_signing_service.verifier
    def unavailable(_):
        raise RuntimeError('Verifier unavailable')
    monkeypatch.setattr(env.services.document_signing_service, 'verifier', unavailable)
    assert not env.document.upload(env.row(), env.definition, 'paper', env.file())['is_valid']
    assert env.document.view(env.row(), env.definition)['files'][0]['can_upload']
    monkeypatch.setattr(env.services.document_signing_service, 'verifier', verifier)
    unrelated = FileStorage(stream=BytesIO(env.pdf.replace(b'200', b'201') + b'\n% signed'), filename='other.pdf', content_type='application/pdf')
    assert not env.document.upload(env.row(), env.definition, 'paper', unrelated)['is_valid']
    env.definition['documents'][0]['allowed_signatures'] = ['profil_zaufany']
    assert not env.document.upload(env.row(), env.definition, 'paper', env.file())['is_valid']
    env.definition['documents'][0]['allowed_signatures'] = ['mszafir', 'profil_zaufany']
    assert env.document.upload(env.row(), env.definition, 'paper', env.file())['completed']


def test_composite_training_agreements_keep_separate_locks(composite_env):
    from models import SubmissionTraining
    env = composite_env('agreement', office_signature=True)
    env.definition['documents'][0]['generation_mode'] = 'per_training'
    snapshots = [{'id': key, 'name': f'Szkolenie {key}', 'price': '25.00', 'capacity': 3} for key in ['a', 'b']]
    with env.repo.session_factory() as db:
        model = db.execute(select(FormSubmission).where(FormSubmission.submission_id == env.public_id)).scalar_one()
        model.selected_trainings = json.dumps(snapshots)
        for snapshot in snapshots:
            db.add(env.services.submission_training_service._new_row(model.id, snapshot))
        model.form_version.definition_json = deepcopy(env.definition)
        db.commit()
    env.enter()
    assert len(env.generated) == 2 and len(env.state()['files']) == 2
    assert [item['label'] for item in env.document.view(env.row(), env.definition)['files']] == [
        'Umowa - Szkolenie a', 'Umowa - Szkolenie b'
    ]
    env.services.workflow_service.run_automatic_steps(env.row(), env.definition)
    assert len(env.generated) == 2
    for key in ['a', 'b']:
        assert env.document.upload(env.row(), env.definition, 'paper', env.file(), instance=key)['is_valid']
        assert env.row()['workflow_stage'] == 'paper'
    with env.repo.session_factory() as db:
        trainings = db.execute(select(SubmissionTraining)).scalars().all()
        assert len(trainings) == 2 and all(t.is_locked for t in trainings)
        assert all(t.training_snapshot['capacity'] == 3 for t in trainings)
    env.verification['signature_count'] = 2
    for key in ['a', 'b']:
        result = env.document.upload(env.row(), env.definition, 'paper', env.file(b'\n% participant signature\n\n% office signature\n'), signer='office', instance=key)
        assert result['completed'] is (key == 'b')
    assert env.row()['workflow_stage'] == 'review'
    assert [item['label'] for item in env.document.completed_files(env.row(), env.definition)] == [
        'Umowa - Szkolenie a', 'Umowa - Szkolenie b'
    ]
    with env.repo.session_factory() as db:
        assert all(t.status == 'agreement_signed_by_office' for t in db.execute(select(SubmissionTraining)).scalars())


def test_training_agreement_upload_cannot_validate_another_instance(composite_env):
    from models import SubmissionTraining

    env = composite_env('agreement', participant_signature=True, upload_required=True)
    env.definition['documents'][0]['generation_mode'] = 'per_training'
    snapshots = [{'id': key, 'name': f'Szkolenie {key}', 'price': '25.00'} for key in ['a', 'b']]
    with env.repo.session_factory() as db:
        model = db.execute(select(FormSubmission).where(FormSubmission.submission_id == env.public_id)).scalar_one()
        model.selected_trainings = json.dumps(snapshots)
        for snapshot in snapshots:
            db.add(env.services.submission_training_service._new_row(model.id, snapshot))
        model.form_version.definition_json = deepcopy(env.definition)
        db.commit()
    env.enter()

    different = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=210, height=210)
    writer.write(different)
    source_b = next(item for item in env.state()['files'] if item['training_key'] == 'b')
    env.services.document_service.document_storage_service.save_pdf(
        storage=env.services.document_service.storage,
        slug=env.row()['form_slug'],
        filename=source_b['filename'],
        document_bytes=different.getvalue(),
        document_type=env.services.document_service._storage_document_type('agreement'),
        signed=False,
    )

    result = env.document.upload(env.row(), env.definition, 'paper', env.file(), instance='b')

    assert result['result'] == 'INVALID_SIGNATURE'
    assert not env.state().get('signatures', {}).get('b', {}).get('participant')
    rejected = [item for item in env.services.submission_document_service.list_documents(env.public_id)
                if item['status'] == 'rejected']
    assert rejected[-1]['signature_validation_result']['reason_code'] == 'DOCUMENT_REVISION_MISMATCH'
    assert rejected[-1]['signature_validation_result']['cryptographically_valid'] is True


def test_agreement_uses_trusted_profile_backend_through_shared_upload_pipeline(
    composite_env, certificate_chain_signed_pdf_factory, monkeypatch
):
    from signature_verifier import verify_signed_pdf

    material = certificate_chain_signed_pdf_factory
    monkeypatch.setenv('SIGNATURE_TRUST_ROOTS', str(material['root_path']))
    monkeypatch.setenv('SIGNATURE_INTERMEDIATE_CERTS', str(material['intermediate_path']))
    monkeypatch.setenv('TRUSTED_PROFILE_PYHANKO_DIAGNOSTIC', 'false')
    env = composite_env('agreement', participant_signature=True, upload_required=True)
    env.definition['documents'][0]['allowed_signatures'] = ['profil_zaufany']
    env.enter()
    signed = material['signed_path'].read_bytes()
    original_end = signed.index(b'%%EOF') + len(b'%%EOF')
    while original_end < len(signed) and signed[original_end:original_end + 1] in {b'\r', b'\n'}:
        original_end += 1
    original = signed[:original_end]
    source = {'filename': env.state()['generated_filename']}
    env.services.document_service.document_storage_service.save_pdf(
        storage=env.services.document_service.storage,
        slug=env.row()['form_slug'],
        filename=source['filename'],
        document_bytes=original,
        document_type=env.services.document_service._storage_document_type('agreement'),
        signed=False,
    )
    monkeypatch.setattr(env.services.document_signing_service, 'verifier', verify_signed_pdf)

    result = env.document.upload(
        env.row(), env.definition, 'paper',
        FileStorage(stream=BytesIO(signed), filename='agreement-pz.pdf', content_type='application/pdf'),
    )

    assert result['result'] == 'VALID_SIGNATURE'
    signed_file = next(item for item in env.services.submission_document_service.list_documents(env.public_id)
                       if item['status'] == 'signed')
    verification = signed_file['signature_validation_result']
    assert verification['provider'] == 'eurocert'
    assert verification['detected_signature_type'] == 'profil_zaufany'
    assert verification['verifier_backend'] == 'trusted_profile_minimal_cms'
    assert verification['signature_count'] == 1
    assert verification['cryptographic_valid'] is True
    assert verification['document_integrity_valid'] is True
    assert verification['certificate_chain_valid'] is True
    assert verification['provider_allowed'] is True


def test_composite_office_route_permissions_and_csrf(composite_env):
    env = composite_env('agreement', participant_signature=False, upload_required=False, office_signature=True)
    env.enter()
    row = env.row()
    base = f"/admin/forms/{env.form_id}/submissions/{row['id']}/document-step/paper"
    client = env.app.test_client()
    assert client.post(base).status_code in {302, 400, 403}
    create_user(env.app, email='viewer@example.com', role='viewer')
    login(client, email='viewer@example.com')
    assert client.get(base).status_code == 403
    client = env.app.test_client()
    create_user(env.app)
    env.app.config['WTF_CSRF_ENABLED'] = True
    login(client)
    assert client.post(base).status_code == 400
    page = client.get(f"/admin/forms/{env.form_id}/submissions/{row['id']}")
    assert page.status_code == 200 and 'data-office-document-step' in page.text
    csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    filename = env.state()['generated_filename']
    assert client.get(f'{base}?filename={filename}').data == env.pdf
    response = client.post(base, data={'csrf_token': csrf, 'document_pdf': (BytesIO(env.pdf + b'\n% office signature\n'), 'office.pdf')})
    assert response.status_code == 302 and env.row()['workflow_stage'] == 'review'


def test_historical_document_nodes_are_not_converted(composite_env):
    env = composite_env()
    historical = deepcopy(env.definition)
    historical['workflow']['steps'][1:2] = [
        {'id': 'paper', 'stage_type': 'document', 'action': 'generate_document', 'document_id': 'declaration', 'next': 'generated'},
        {'id': 'generated', 'stage_type': 'system', 'action': 'none', 'next': 'signature'},
        {'id': 'signature', 'stage_type': 'document', 'action': 'await_signature', 'document_id': 'declaration', 'next': 'signed'},
        {'id': 'signed', 'stage_type': 'system', 'action': 'none', 'next': 'review'},
    ]
    with env.repo.session_factory() as db:
        version = db.get(FormVersion, env.row()['form_version_id'])
        version.definition_json = historical
        db.commit()
    env.services.workflow_service.advance_after_action(env.row(), historical)
    assert env.row()['workflow_stage'] == 'signature'
    assert env.document.view(env.row(), historical) is None
    assert env.row()['declaration_filename']
    assert env.services.workflow_service.definition_for(env.row()) == historical


def test_composite_recovery_reuses_generated_file_and_verified_signature(composite_env, monkeypatch):
    env = composite_env()
    save = env.repo.save_document_step
    def interrupted_save(submission_id, step_id, state, token, event, actor='system', *, field_values=None):
        if event == 'DOCUMENT_GROUP_READY':
            raise RuntimeError('Interrupted after file persistence')
        return save(submission_id, step_id, state, token, event, actor, field_values=field_values)
    monkeypatch.setattr(env.repo, 'save_document_step', interrupted_save)
    with pytest.raises(RuntimeError):
        env.enter()
    monkeypatch.setattr(env.repo, 'save_document_step', save)
    env.services.workflow_service.run_automatic_steps(env.row(), env.definition)
    assert len(env.generated) == 1 and env.state()['generated_filename']
    complete = env.document._complete
    def interrupted_completion(*args):
        raise RuntimeError('Interrupted after verification')
    monkeypatch.setattr(env.document, '_complete', interrupted_completion)
    with pytest.raises(RuntimeError):
        env.document.upload(env.row(), env.definition, 'paper', env.file())
    monkeypatch.setattr(env.document, '_complete', complete)
    assert env.document.upload(env.row(), env.definition, 'paper', env.file())['completed']
    assert env.row()['workflow_stage'] == 'review'
    with env.repo.session_factory() as db:
        events = db.execute(select(SubmissionWorkflowEvent).where(SubmissionWorkflowEvent.public_submission_id == env.public_id)).scalars().all()
        assert sum(e.source == 'DOCUMENT_STEP_COMPLETED' for e in events) == 1
        assert sum(e.source == 'DOCUMENT_STEP_COMPLETED' for e in events) == 1


def test_composite_generation_isolated_between_submissions(composite_env):
    env = composite_env()
    env.definition['documents'][0]['filename_pattern'] = 'document.pdf'
    with env.repo.session_factory() as db:
        original = db.execute(select(FormSubmission).where(FormSubmission.submission_id == env.public_id)).scalar_one()
        original.form_version.definition_json = deepcopy(env.definition)
        second_id = str(uuid4())
        db.add(FormSubmission(submission_id=second_id, form_slug=original.form_slug, form_version_id=original.form_version_id,
            process_status='FORM_SUBMITTED', workflow_stage='intake', workflow_step='intake'))
        db.commit()
    env.enter()
    env.services.workflow_service.advance_after_action(env.repo.get_by_id(second_id), env.definition)
    first = env.state()['generated_filename']
    second = document_item_state(env.repo.get_by_id(second_id), 'paper', 'declaration',
        primary_document_id='declaration')['generated_filename']
    assert first != second
    assert env.document.download(env.row(), env.definition, 'paper', first) == env.pdf
    assert env.document.download(env.repo.get_by_id(second_id), env.definition, 'paper', second) == env.pdf


def test_new_draft_has_one_stage_per_document_without_implicit_decision():
    from services.form_config_service import FormConfigService
    from services.workflow_config_service import WorkflowConfigValidator
    service = FormConfigService()
    definition = service.normalize_form_config(service.build_new_draft_workflow({
        'title': 'Nowy formularz', 'fields': [], 'documents': [
            {'id': key, 'label': label, 'template_html': '<p>Dokument</p>'}
            for key, label in [('declaration', 'Deklaracja'), ('agreement', 'Umowa'), ('statement', 'Oświadczenie')]]
    }))
    steps = definition['workflow']['steps']
    assert [s['id'] for s in steps] == ['submission', 'declaration', 'agreement', 'statement', 'completed']
    assert all(s['stage_type'] == 'documents' and s['document_lifecycle'] == 'group'
               and not s.get('document_id') for s in steps[1:-1])
    assert {document['workflow_step'] for document in definition['documents']} == {
        'declaration', 'agreement', 'statement'}
    assert not any(s['stage_type'] == 'decision' for s in steps)
    assert WorkflowConfigValidator().validate(definition['workflow'], definition) == []


@pytest.mark.parametrize('stale_mirror', [False, True])
def test_composite_draft_save_reload_uses_version_documents(admin_app, stale_mirror):
    from models import Form
    from services.form_config_service import FormConfigService
    service = FormConfigService()
    definition = service.normalize_form_config(service.build_new_draft_workflow({
        'title': 'Draft', 'fields': [], 'documents': [
            {'id': key, 'label': key, 'template_html': '<p>Dokument</p>'}
            for key in ('declaration', 'agreement')]}))
    for step in definition['workflow']['steps'][1:-1]:
        step['document_options'] = {'generate': True, 'download': False, 'participant_signature': True,
                                    'upload_required': True, 'office_signature': False, 'signature_verification': True}
        step['document_instructions'] = {key: {'description': f'<p>{key}</p>', 'next_action': 'Dalej'}
                                         for key in ('ready', 'upload', 'verification_failed')}
    definition['fields'] = [{'name': 'draft_contact', 'label': 'Kontakt', 'type': 'email',
        'availability': [{'step': 'submission', 'visible': True, 'editable': True, 'required': True}]}]
    form_id = create_form(admin_app, slug='draft-roundtrip')
    create_user(admin_app)
    repo = admin_app.extensions['services'].submission_repository
    with repo.session_factory() as db:
        form = db.get(Form, form_id)
        published = FormVersion(form_id=form_id, version_major=1, version_minor=0,
            version_label='1.0', status='published', definition_json=deepcopy(form.definition_json))
        db.add(published)
        old_published = deepcopy(published.definition_json)
        draft = FormVersion(form_id=form_id, version_major=1, version_minor=1,
            version_label='1.1', status='draft', definition_json=deepcopy(definition))
        db.add(draft)
        if not stale_mirror:
            form.definition_json = deepcopy(definition)
        db.commit()
        draft_id, published_id = draft.id, published.id
    client = admin_app.test_client()
    login(client)
    url = f'/admin/forms/{form_id}/edit?tab=workflow'
    csrf = client.get(url).text.split('name="csrf_token" value="')[1].split('"')[0]
    payload = {'csrf_token': csrf, 'active_tab': 'workflow', 'workflow_controls_present': '1',
               'workflow_builder_json': json.dumps(definition['workflow'])}
    response = client.post(url, data=payload)
    assert response.status_code == 302, __import__('re').findall(r'<li>(.*?)</li>', response.text)
    with repo.session_factory() as db:
        saved = db.get(FormVersion, draft_id).definition_json
        expected = definition['workflow']['steps']
        assert [(s['id'], s['type'], s.get('next') or '') for s in saved['workflow']['steps']] == [
            (s['id'], s['type'], s.get('next') or '') for s in expected]
        for before, after in zip(expected[1:-1], saved['workflow']['steps'][1:-1]):
            for key in ('document_lifecycle', 'document_options', 'document_instructions'):
                assert after[key] == before[key]
            assert not after.get('document_id')
        assert [f['name'] for f in saved['fields']] == ['draft_contact']
        assert saved['fields'][0]['availability'] == definition['fields'][0]['availability']
        assert db.get(FormVersion, published_id).definition_json == old_published
        saved_snapshot = deepcopy(saved)
    assert client.get(url).status_code == 200
    bad = deepcopy(definition['workflow'])
    bad['steps'][1]['next'] = ''
    response = client.post(url, data={**payload, 'name': 'Must not persist', 'workflow_builder_json': json.dumps(bad)})
    assert response.status_code == 400
    assert 'kolejny etap' in response.text.lower()
    with repo.session_factory() as db:
        assert db.get(FormVersion, draft_id).definition_json == saved_snapshot
        assert db.get(Form, form_id).name != 'Must not persist'


def test_admin_import_without_workflow_does_not_add_a_decision(admin_app):
    from models import Form
    create_user(admin_app)
    client = admin_app.test_client()
    login(client)
    csrf = client.get('/admin/forms/upload').text.split('name="csrf_token" value="')[1].split('"')[0]
    response = client.post('/admin/forms/upload', data={
        'csrf_token': csrf, 'slug': 'new-business-flow', 'form_file': (BytesIO(json.dumps({'title': 'Nowy formularz', 'fields': []}).encode()), 'form.json')})
    assert response.status_code == 302
    with admin_app.extensions['services'].submission_repository.session_factory() as db:
        form = db.execute(select(Form).where(Form.slug == 'new-business-flow')).scalar_one()
        workflow = form.definition_json['workflow']
        assert workflow['flow_mode'] == 'explicit'
        assert [s['id'] for s in workflow['steps']] == ['submission', 'completed']


def test_draft_conversion_collapses_phases_and_preserves_source_and_instructions():
    from services.workflow_config_service import collapse_draft_document_stages
    from services.form_config_service import FormConfigService
    raw = {'title': 'Draft', 'fields': [], 'documents': [
        {'id': key, 'label': label, 'template_html': '<p>Dokument</p>'} for key, label in [('declaration', 'Deklaracja'), ('agreement', 'Umowa')]],
        'workflow': {'flow_mode': 'explicit', 'initial_step': 'submission', 'steps': [
            {'id': 'submission', 'type': 'form_submit', 'next': 'declaration_ready'},
            {'id': 'declaration_ready', 'status': 'DECLARATION_READY', 'description': 'Pobierz deklarację.', 'next': 'declaration_signature'},
            {'id': 'declaration_signature', 'status': 'DECLARATION_WAITING_FOR_SIGNATURE', 'description': 'Podpisz deklarację.', 'next': 'agreement_ready'},
            {'id': 'agreement_ready', 'status': 'AGREEMENT_READY', 'next': 'agreement_signature'},
            {'id': 'agreement_signature', 'status': 'AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE', 'next': 'office_signature'},
            {'id': 'office_signature', 'status': 'AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE', 'next': 'completed'},
            {'id': 'completed', 'final': True} ]}}
    source = FormConfigService().normalize_form_config(raw)
    original = deepcopy(source)
    prepared = collapse_draft_document_stages(source)
    steps = prepared['workflow']['steps']
    assert [s['id'] for s in steps] == ['submission', 'declaration', 'agreement', 'completed']
    assert [s['admin_label'] for s in steps[1:3]] == ['Deklaracja', 'Umowa']
    assert steps[1]['document_instructions']['ready']['description'] == 'Pobierz deklarację.'
    assert steps[1]['document_instructions']['awaiting_signature']['description'] == 'Podpisz deklarację.'
    assert steps[2]['document_options']['office_signature']
    assert source == original
    assert collapse_draft_document_stages(prepared) == prepared


def test_draft_conversion_keeps_business_decisions_and_field_permissions():
    from services.workflow_config_service import collapse_draft_document_stages
    definition = {'documents': [{'id': 'statement', 'label': 'Oświadczenie'}], 'fields': [], 'workflow': {
        'flow_mode': 'explicit', 'initial_step': 'prepare', 'steps': [
            {'id': 'prepare', 'document_id': 'statement', 'action': 'generate_document', 'next': 'sign'},
            {'id': 'sign', 'document_id': 'statement', 'action': 'await_signature', 'next': 'review'},
            {'id': 'review', 'stage_type': 'decision', 'next': 'completed'}, {'id': 'completed', 'final': True}]}}
    result = collapse_draft_document_stages(definition)
    assert [s['id'] for s in result['workflow']['steps']] == ['statement', 'review', 'completed']
    definition['fields'] = [{'name': 'secret', 'availability': [{'step': 'sign', 'visible': True, 'editable': True}]}]
    result = collapse_draft_document_stages(definition)
    assert [s['id'] for s in result['workflow']['steps']] == ['prepare', 'sign', 'review', 'completed']


def split_document_draft(*, already_composite=False):
    from services.form_config_service import FormConfigService
    phases = [
        ('submission', 'FORM_SUBMITTED', ''),
        ('declaration_ready', 'DECLARATION_READY', 'declaration'),
        ('declaration_signature', 'DECLARATION_WAITING_FOR_SIGNATURE', 'declaration'),
        ('agreement_ready', 'AGREEMENT_READY', 'agreement'),
        ('agreement_participant_signature', 'AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE', 'agreement'),
        ('agreement_office_signature', 'AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE', 'agreement'),
        ('completed', 'COMPLETED', ''),
    ]
    steps = []
    for index, (key, status, document_id) in enumerate(phases):
        step = {'id': key, 'status': status, 'admin_label': key, 'user_label': status,
                'next': phases[index + 1][0] if index + 1 < len(phases) else '',
                'type': 'document' if document_id else 'form_submit' if index == 0 else 'end',
                'stage_type': 'document' if document_id else 'user_action' if index == 0 else 'final'}
        if document_id:
            step.update(document_id=document_id, action='await_signature' if 'signature' in key else 'generate_document')
            if already_composite:
                step.update(document_lifecycle='composite', action='none')
        steps.append(step)
    steps[1]['description'] = 'Pobierz deklarację'
    steps[1]['triggers'] = ['document_generated']
    definition = {'title': 'Scalany draft', 'fields': [], 'documents': [
        {'id': key, 'label': label, 'template_html': '<p>Dokument</p>'}
        for key, label in [('declaration', 'Deklaracja'), ('agreement', 'Umowa')]],
        'workflow': {'schema_version': 2, 'flow_mode': 'explicit', 'initial_step': 'submission', 'steps': steps}}
    return FormConfigService().normalize_form_config(definition)


@pytest.mark.parametrize('already_composite', [False, True])
def test_draft_conversion_really_merges_seven_nodes_and_references(already_composite):
    from services.workflow_config_service import collapse_draft_document_stages, WorkflowConfigValidator
    definition = split_document_draft(already_composite=already_composite)
    definition['workflow']['steps'][0]['transitions'] = [{'when': {'field': 'choice', 'equals': 'yes'}, 'next': 'declaration_signature'}]
    definition['workflow']['steps'][0]['next'] = ''
    definition['workflow']['decision_settings'] = [{'id': 'custom', 'stage_id': 'declaration_signature',
        'workflow_stage_id': 'declaration_signature', 'decision_stage': 'declaration_signature', 'yes_status': 'agreement_ready'}]
    definition['documents'][0]['available_at_step'] = 'declaration_signature'
    definition['correction'] = {'target_step': 'declaration_ready', 'return_step': 'agreement_participant_signature'}
    original = deepcopy(definition)
    converted = collapse_draft_document_stages(definition)
    steps = converted['workflow']['steps']
    assert [s['id'] for s in steps] == ['submission', 'declaration', 'agreement', 'completed']
    assert steps[0]['transitions'][0]['next'] == 'declaration'
    assert steps[1]['next'] == 'agreement' and steps[2]['next'] == 'completed'
    assert steps[1]['document_instructions']['ready']['description'] == 'Pobierz deklarację'
    assert steps[2]['document_options']['office_signature'] is True
    assert converted['correction'] == {'target_step': 'declaration', 'return_step': 'agreement'}
    assert converted['documents'][0]['available_at_step'] == 'declaration'
    assert converted['workflow']['decision_settings'][0]['yes_status'] == 'agreement'
    assert all(converted['workflow']['decision_settings'][0][key] == 'declaration'
        for key in ('stage_id', 'workflow_stage_id', 'decision_stage', 'step_id'))
    assert WorkflowConfigValidator().validate(converted['workflow'], converted) == []
    assert definition == original
    assert collapse_draft_document_stages(converted) == converted


@pytest.mark.parametrize('already_composite', [False, True])
@pytest.mark.parametrize('builder_converted', [False, True])
def test_split_draft_save_reload_persists_four_nodes(admin_app, already_composite, builder_converted):
    import re
    from models import Form
    from services.workflow_config_service import collapse_draft_document_stages, WorkflowConfigValidator
    definition = split_document_draft(already_composite=already_composite)
    definition['fields'] = [{'name': 'contact', 'label': 'Kontakt', 'type': 'text', 'availability': [
        {'step': step, 'visible': True, 'editable': False, 'required': False}
        for step in ('declaration_ready', 'declaration_signature')]}]
    definition['workflow']['diagram_layout'] = {'nodes': {
        'declaration_ready': {'x': 100, 'y': 200}, 'agreement_office_signature': {'x': 300, 'y': 400}}}
    form_id = create_form(admin_app, slug='split-roundtrip')
    create_user(admin_app)
    repo = admin_app.extensions['services'].submission_repository
    with repo.session_factory() as db:
        published = FormVersion(form_id=form_id, version_major=1, version_minor=0,
            version_label='1.0', status='published', definition_json=deepcopy(definition))
        draft = FormVersion(form_id=form_id, version_major=1, version_minor=1,
            version_label='1.1', status='draft', definition_json=deepcopy(definition))
        db.add_all([published, draft])
        db.flush()
        historical = FormSubmission(submission_id=str(uuid4()), form_slug='split-roundtrip', form_version_id=published.id,
            workflow_step='agreement_office_signature', workflow_stage='agreement_office_signature',
            process_status='AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE', document_states={'legacy': {'signed': True}})
        db.add(historical)
        db.commit()
        draft_id, published_id, historical_id = draft.id, published.id, historical.id
    client = admin_app.test_client()
    login(client)
    url = f'/admin/forms/{form_id}/edit?tab=workflow'
    page = client.get(url)
    assert page.status_code == 200
    csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    # Support both the current converted browser payload and a stale seven-node payload.
    workflow = deepcopy(definition['workflow'])
    if builder_converted:
        workflow = collapse_draft_document_stages(definition)['workflow']
        workflow['document_stage_conversion'] = True
    response = client.post(url, data={'csrf_token': csrf, 'active_tab': 'workflow',
        'workflow_controls_present': '1', 'workflow_builder_json': json.dumps(workflow)})
    assert response.status_code == 302, re.findall(r'<li>(.*?)</li>', response.text)
    expected_ids = ['submission', 'declaration', 'agreement', 'completed']
    with repo.session_factory() as db:
        saved = db.get(FormVersion, draft_id).definition_json
        steps = saved['workflow']['steps']
        assert [s['id'] for s in steps] == expected_ids
        assert [s['next'] for s in steps] == ['declaration', 'agreement', 'completed', '']
        assert steps[1]['document_instructions']['ready']['description'] == 'Pobierz deklarację'
        assert steps[2]['document_options']['office_signature'] is True
        assert [a['step'] for a in saved['fields'][0]['availability']] == ['declaration']
        assert set(saved['workflow']['diagram_layout']['nodes']) == {'declaration', 'agreement'}
        assert WorkflowConfigValidator().validate(saved['workflow'], saved) == []
        assert db.get(Form, form_id).definition_json['workflow']['steps'] == steps
        assert db.get(FormVersion, published_id).definition_json == definition
        historical = db.get(FormSubmission, historical_id)
        assert historical.form_version_id == published_id
        assert historical.workflow_step == historical.workflow_stage == 'agreement_office_signature'
        assert historical.process_status == 'AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE'
        assert historical.document_states == {'legacy': {'signed': True}}
    page = client.get(url)
    assert page.status_code == 200
    # The template includes a prototype; count actual serialized stage cards only.
    cards = [json.loads(s) for s in re.findall(r'data-workflow-step-source>(.*?)</script>', page.text) if json.loads(s)]
    assert [s['id'] for s in cards] == expected_ids
    assert 'data-document-conversion-notice' not in page.text


def test_draft_conversion_keeps_decision_and_remaps_legacy_next_step():
    from services.workflow_config_service import collapse_draft_document_stages, WorkflowConfigValidator
    definition = split_document_draft()
    steps = definition['workflow']['steps']
    steps[0]['next'] = 'review'
    steps.insert(1, {'id': 'review', 'admin_label': 'Decyzja', 'user_label': 'Decyzja',
        'stage_type': 'decision', 'type': 'manual_decision', 'status': 'WAITING_FOR_OFFICER_DECISION',
        'decision_scope': 'submission', 'decision_name': 'Decyzja'})
    definition['workflow']['decision_types'] = [
        {'code': 'yes', 'label': 'Tak', 'scope': 'submission', 'step_id': 'review', 'target_step': 'declaration_ready'},
        {'code': 'no', 'label': 'Nie', 'scope': 'submission', 'step_id': 'review', 'target_step': 'completed'}]
    for step in steps[2:-1]:
        step['next_step'] = step.pop('next')
    converted = collapse_draft_document_stages(definition)
    assert [s['id'] for s in converted['workflow']['steps']] == ['submission', 'review', 'declaration', 'agreement', 'completed']
    assert converted['workflow']['decision_types'][0]['target_step'] == 'declaration'
    assert converted['workflow']['steps'][2]['next_step'] == 'agreement'
    assert converted['workflow']['steps'][3]['next_step'] == 'completed'
    assert WorkflowConfigValidator().validate(converted['workflow'], converted) == []
    definition['workflow']['initial_step'] = 'declaration_signature'
    assert collapse_draft_document_stages(definition)['workflow']['initial_step'] == 'declaration'


@pytest.mark.parametrize('change', [
    {'document_id': 'missing'}, {'next': ''},
    {'document_options': {'participant_signature': True, 'upload_required': False}},
])
def test_composite_validator_reports_real_configuration_errors(change):
    from services.workflow_config_service import collapse_draft_document_stages, WorkflowConfigValidator
    definition = collapse_draft_document_stages(split_document_draft())
    validator = WorkflowConfigValidator()
    assert validator.validate(definition['workflow'], definition) == []
    definition['workflow']['steps'][1].update(change)
    errors = validator.validate(definition['workflow'], definition)
    assert errors
    assert any('declaration' in error for error in errors)


@pytest.mark.parametrize('failure', ['renderer', 'metadata'])
def test_generation_failure_never_exposes_ready_or_download(composite_env, monkeypatch, failure):
    env = composite_env()
    if failure == 'renderer':
        def fail(**kwargs):
            raise RuntimeError('Renderer unavailable')
        monkeypatch.setattr(env.services.document_service.pdf_render_service, 'render_document_pdf_bytes', fail)
    else:
        monkeypatch.setattr(env.services.document_service.submission_document_service, 'record_generated_document', lambda **kwargs: False)
    with pytest.raises(RuntimeError):
        env.enter()
    assert env.row()['workflow_stage'] == 'paper'
    assert env.state()['substate'] == 'failed'
    view = env.document.view(env.row(), env.definition)
    assert view['files'] == [] and view['can_retry']
    page = env.app.test_client().get(f"/do-podpisania?submission_id={env.public_id}&token={env.row()['access_token']}")
    assert page.status_code == 200 and '/paper/download/' not in page.text


def test_composite_uses_real_document_builder_pdf_and_metadata(composite_env, monkeypatch):
    from pypdf import PdfReader
    from services.documents.pdf_render_service import PdfRenderService
    from pdf_generator import generate_pdf_from_html
    env = composite_env()
    env.definition['documents'][0].update(template_source='builder', builder_document={
        'version': 1, 'blocks': [{'id': 'text', 'type': 'paragraph', 'content': 'Deklaracja uczestnika testowego'}]})
    env.definition['documents'][0].pop('template_html')
    with env.repo.session_factory() as db:
        db.get(FormVersion, env.row()['form_version_id']).definition_json = deepcopy(env.definition)
        db.commit()
    monkeypatch.setattr(env.services.document_service, 'pdf_render_service', PdfRenderService(html_renderer=generate_pdf_from_html))
    env.enter()
    assert env.state()['substate'] == 'ready'
    files = env.services.document_service.submission_document_service.list_documents(env.public_id)
    assert len(files) == 1 and files[0]['document_id'] == 'declaration'
    data = env.document.download(env.row(), env.definition, 'paper', env.state()['generated_filename'])
    assert data.startswith(b'%PDF-')
    assert 'Deklaracja uczestnika testowego' in ''.join(p.extract_text() for p in PdfReader(BytesIO(data)).pages)
