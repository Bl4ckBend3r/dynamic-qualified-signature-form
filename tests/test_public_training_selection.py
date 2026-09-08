from copy import deepcopy
import json

import pytest
from lxml import html
from werkzeug.datastructures import MultiDict

from models import Form, FormVersion, FormSubmission, SubmissionTraining, SubmissionFile
from test_admin_panel import admin_app
from test_composite_document_workflow import composite_env, configure_document_fields


@pytest.fixture
def picker_env(composite_env):
    env = composite_env()
    configure_document_fields(env)
    field = env.definition["documents"][0]["fields"][1]
    field["catalog"].append({"id": "course-b", "name": "Szkolenie B", "price": "200", "capacity": 10})
    with env.repo.session_factory() as db:
        db.get(Form, env.form_id).definition_json = deepcopy(env.definition)
        db.get(FormVersion, env.row()["form_version_id"]).definition_json = deepcopy(env.definition)
        db.commit()
    env.enter()
    env.document.save_fields(env.row(), env.definition, "paper", MultiDict({
        "deklaracja_18_lat": "Tak", "wybor_szkolen": "course-a", "declaration_note": "Dane"}))
    env.app.config.update(PUBLIC_CSRF_ENABLED=True, WTF_CSRF_ENABLED=True)
    env.client = env.app.test_client()
    env.url = f"/submissions/{env.public_id}/trainings?token={env.row()['access_token']}"
    env.status_url = f"/do-podpisania?submission_id={env.public_id}&token={env.row()['access_token']}"
    page = env.client.get(env.url)
    assert page.status_code == 200
    env.csrf = html.fromstring(page.text).xpath('//input[@name="csrf_token"]/@value')[0]
    env.post = lambda ids, **extra: env.client.post(env.url, data={"csrf_token": env.csrf, "wybor_szkolen": ids, **extra})
    return env


@pytest.mark.parametrize("signed", [False, True])
def test_status_picker_available_before_and_after_signed_declaration(picker_env, signed):
    env = picker_env
    if signed:
        assert env.document.upload(env.row(), env.definition, "paper", env.file())["completed"]
    page = env.client.get(env.status_url)
    assert page.status_code == 200
    doc = html.fromstring(page.text)
    assert doc.xpath('//form[@data-training-picker]')
    assert doc.xpath('//input[@value="course-a" and @checked and not(@disabled)]')
    assert doc.xpath('//input[@value="course-b" and not(@disabled)]')
    previous_stage = env.row()["workflow_stage"]
    previous_states = deepcopy(env.row()["document_states"])
    previous_snapshot = deepcopy(env.row()["data_json"].get("_signed_declaration_snapshot"))
    response = env.post(["course-a", "course-b"], price="1", name="Injected")
    assert response.status_code == 302 and "token=" in response.location
    assert {x["id"] for x in json.loads(env.row()["selected_trainings"])} == {"course-a", "course-b"}
    assert env.row()["data_json"]["wybor_szkolen"] == env.row()["selected_trainings"]
    assert env.row()["workflow_stage"] == previous_stage
    assert env.row()["document_states"] == previous_states
    assert env.row()["data_json"].get("_signed_declaration_snapshot") == previous_snapshot
    assert "Wybór szkoleń został zapisany." in env.client.get(response.location).text
    with env.repo.session_factory() as db:
        added = db.query(SubmissionTraining).filter_by(training_id="course-b").one()
        assert added.training_name_snapshot == "Szkolenie B"
        assert str(added.training_price_snapshot) == "200.00"


def test_closed_after_render_rejects_post_and_keeps_readonly_selection(picker_env):
    env = picker_env
    before = env.row()["selected_trainings"]
    with env.repo.session_factory() as db:
        db.get(Form, env.form_id).training_selection_open = False
        db.commit()
    assert env.post(["course-a", "course-b"]).status_code == 409
    assert env.row()["selected_trainings"] == before
    page = env.client.get(env.status_url)
    assert "Wybór szkoleń jest zamknięty." in page.text
    doc = html.fromstring(page.text)
    assert doc.xpath('//input[@value="course-a" and @checked and @disabled]')
    assert not doc.xpath('//button[@data-training-submit]')


@pytest.mark.parametrize("ids,message", [(["outside"], "nie jest już dostępne"), ([], "co najmniej jedno")])
def test_invalid_selection_is_atomic(picker_env, ids, message):
    env = picker_env
    before = env.row()["selected_trainings"]
    response = env.post(ids)
    assert response.status_code == 400 and message in response.text
    assert env.row()["selected_trainings"] == before


def test_current_catalog_and_versioned_limit_checked_on_post(picker_env):
    env = picker_env
    with env.repo.session_factory() as db:
        form = db.get(Form, env.form_id)
        definition = deepcopy(form.definition_json)
        definition["documents"][0]["fields"][1]["catalog"][1]["price"] = "3000"
        form.definition_json = definition
        db.commit()
    before = env.row()["selected_trainings"]
    response = env.post(["course-a", "course-b"])
    assert response.status_code == 400 and "przekracza limit" in response.text
    assert env.row()["selected_trainings"] == before


def test_forged_post_with_two_trainings_from_one_selection_group_is_rejected(picker_env):
    env = picker_env
    with env.repo.session_factory() as db:
        form = db.get(Form, env.form_id)
        definition = deepcopy(form.definition_json)
        catalog = definition["documents"][0]["fields"][1]["catalog"]
        for training in catalog:
            training["selection_group"] = "Pierwsza pomoc"
        form.definition_json = definition
        db.commit()

    before = env.row()["selected_trainings"]
    response = env.post(["course-a", "course-b"])

    assert response.status_code == 400
    assert "Możesz wybrać tylko jeden termin szkolenia „Pierwsza pomoc”." in response.text
    assert env.row()["selected_trainings"] == before

    page = env.client.get(env.status_url)
    doc = html.fromstring(page.text)
    assert len(doc.xpath('//input[@data-selection-group="Pierwsza pomoc"]')) == 2


@pytest.mark.parametrize("status,locked", [("locked", True), ("agreement_signed_by_office", False)])
def test_locked_training_and_signed_history_survive_other_changes(picker_env, status, locked):
    env = picker_env
    with env.repo.session_factory() as db:
        row = db.query(SubmissionTraining).filter_by(training_id="course-a").one()
        row.status, row.is_locked = status, locked
        file = SubmissionFile(submission_id=row.submission_id, public_submission_id=env.public_id,
            form_slug=env.row()["form_slug"], filename="historical-agreement.pdf", document_type="agreement_signed_by_office",
            document_id="agreement", training_key="course-a", signed=True, status="signed", storage_path="history/agreement.pdf")
        db.add(file)
        db.commit()
    response = env.post(["course-b"])
    assert response.status_code == 400 and "zablokowanego" in response.text
    assert env.post(["course-a", "course-b"]).status_code == 302
    with env.repo.session_factory() as db:
        file = db.query(SubmissionFile).filter_by(filename="historical-agreement.pdf").one()
        assert file.status == "signed"
        assert db.query(SubmissionTraining).filter_by(training_id="course-a").one().status == status
    doc = html.fromstring(env.client.get(env.status_url).text)
    assert doc.xpath('//input[@type="checkbox" and @value="course-a" and @disabled]')


def test_access_and_csrf(picker_env):
    env = picker_env
    url = env.url.split("?")[0]
    assert env.client.get(url).status_code == 404
    assert env.client.post(url, data={"csrf_token": env.csrf}).status_code == 404
    assert env.client.post(url + "?token=wrong", data={"csrf_token": env.csrf}).status_code == 404
    assert env.client.post(env.url, data={"wybor_szkolen": "course-b"}).status_code == 400
    assert env.client.post(env.url.replace(env.public_id, "different-id"), data={"csrf_token": env.csrf}).status_code == 404


def test_dependent_signed_declaration_requires_new_revision_without_deleting_file(picker_env):
    env = picker_env
    document = env.definition["documents"][0]
    document["builder_document"]["blocks"][0]["content"] += " {% for t in selected_trainings %}{{ t.name }}{% endfor %}"
    with env.repo.session_factory() as db:
        db.get(FormVersion, env.row()["form_version_id"]).definition_json = deepcopy(env.definition)
        db.commit()
    assert env.document.upload(env.row(), env.definition, "paper", env.file())["completed"]
    signed_snapshot = deepcopy(env.row()["data_json"]["_signed_declaration_snapshot"])
    old_files = env.services.submission_document_service.list_documents(env.public_id)
    assert env.post(["course-a", "course-b"]).status_code == 302
    assert env.state()["substate"] == "collect_data"
    assert not env.row()["declaration_signature_valid"]
    assert "_signed_declaration_snapshot" not in env.row()["data_json"]
    assert env.row()["data_json"]["_declaration_signature_history"][-1]["document_sha256"] == signed_snapshot["document_sha256"]
    files = env.services.submission_document_service.list_documents(env.public_id)
    assert {f["filename"] for f in files} == {f["filename"] for f in old_files}
    assert all(f["status"] == "superseded" for f in files)
    env.document.save_fields(env.row(), env.definition, "paper", MultiDict({"deklaracja_18_lat": "Tak",
        "wybor_szkolen": ["course-a", "course-b"], "declaration_note": "Dane"}))
    assert env.state()["files"][0]["filename"] not in {f["filename"] for f in old_files}
    assert {x["id"] for x in env.generated[-1]["context"]["selected_trainings"]} == {"course-a", "course-b"}


def test_added_training_gets_agreement_without_regenerating_signed_training(picker_env):
    env = picker_env
    assert env.document.upload(env.row(), env.definition, "paper", env.file())["completed"]
    env.definition["documents"].append({"id": "agreement", "label": "Umowa", "kind": "generated_pdf",
        "template_html": "<p>{{ training.name }}</p>", "repeat_over": "selected_trainings", "repeat_item_alias": "training",
        "filename_pattern": "agreement-{training_id}.pdf"})
    env.definition["workflow"]["steps"].append({"id": "contracts", "admin_label": "Umowy", "user_label": "Umowy",
        "status": "AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE", "stage_type": "document", "type": "document",
        "document_lifecycle": "composite", "document_id": "agreement", "next": "done",
        "document_options": {"participant_signature": True, "office_signature": True}})
    with env.repo.session_factory() as db:
        db.get(FormVersion, env.row()["form_version_id"]).definition_json = deepcopy(env.definition)
        db.commit()
    env.repo.update(env.public_id, {"workflow_stage": "contracts", "workflow_step": "contracts"})
    env.document.start(env.row(), env.definition)
    assert env.document.upload(env.row(), env.definition, "contracts", env.file(), instance="course-a")["result"] == "VALID_SIGNATURE"
    from services.documents.document_workflow_service import document_step_state
    old_state = document_step_state(env.row(), "contracts")
    old_signatures = deepcopy(old_state["signatures"])
    old_files = {f["filename"]: deepcopy(f) for f in env.services.submission_document_service.list_documents(env.public_id)}
    count = len(env.generated)
    assert env.post(["course-a", "course-b"]).status_code == 302
    env.document.start(env.row(), env.definition)
    state = document_step_state(env.row(), "contracts")
    assert {f["training_key"] for f in state["files"]} == {"course-a", "course-b"}
    assert state["signatures"] == old_signatures
    assert len(env.generated) == count + 1
    assert env.generated[-1]["context"]["training"]["id"] == "course-b"
    current_files = {f["filename"]: f for f in env.services.submission_document_service.list_documents(env.public_id)}
    for filename, old in old_files.items():
        assert current_files[filename]["status"] == old["status"]
        assert current_files[filename]["checksum_sha256"] == old["checksum_sha256"]


def test_removed_current_catalog_id_and_disabled_configuration_rejected(picker_env):
    env = picker_env
    with env.repo.session_factory() as db:
        form = db.get(Form, env.form_id)
        definition = deepcopy(form.definition_json)
        definition["documents"][0]["fields"][1]["catalog"].pop()
        form.definition_json = definition
        db.commit()
    assert env.post(["course-a", "course-b"]).status_code == 400
    with env.repo.session_factory() as db:
        version = db.get(FormVersion, env.row()["form_version_id"])
        definition = deepcopy(version.definition_json)
        definition["documents"][0]["fields"][1]["enabled"] = False
        version.definition_json = definition
        db.commit()
    assert env.post(["course-a"]).status_code == 409
    doc = html.fromstring(env.client.get(env.status_url).text)
    assert doc.xpath('//input[@type="checkbox" and @value="course-a" and @checked and @disabled]')


def test_unsigned_unselection_keeps_row_history(picker_env):
    env = picker_env
    assert env.post(["course-b"]).status_code == 302
    assert [item["id"] for item in json.loads(env.row()["selected_trainings"])] == ["course-b"]
    with env.repo.session_factory() as db:
        row = db.query(SubmissionTraining).filter_by(training_id="course-a").one()
        assert row.status == "unselected" and row.unselected_at


@pytest.mark.parametrize("expression,dependent", [("{{ submission.first_name }}", False),
    ("{{ submission['wybor_szkolen'] }}", True), ("{{ data_json.wybor_szkolen }}", True)])
def test_declaration_dependencies_distinguish_training_from_other_fields(picker_env, expression, dependent):
    env = picker_env
    env.definition["documents"][0]["builder_document"]["blocks"][0]["content"] = expression
    with env.repo.session_factory() as db:
        db.get(FormVersion, env.row()["form_version_id"]).definition_json = deepcopy(env.definition)
        db.commit()
    before = deepcopy(env.state())
    assert env.post(["course-a", "course-b"]).status_code == 302
    assert (env.state()["substate"] == "collect_data") is dependent
    if not dependent:
        assert env.state() == before


def test_document_fields_cannot_bypass_closed_recruitment(picker_env):
    env = picker_env
    # An unsigned, unconfirmed declaration is still in its data collection stage.
    with env.repo.session_factory() as db:
        model = db.get(FormSubmission, env.row()["id"])
        states = deepcopy(model.document_states)
        states["workflow_documents"]["paper"] = {"document_id": "declaration", "substate": "collect_data"}
        model.document_states = states
        db.get(Form, env.form_id).training_selection_open = False
        db.commit()
    before = env.row()["selected_trainings"]
    page = html.fromstring(env.client.get(env.status_url).text)
    assert page.xpath('//input[@type="checkbox" and @value="course-a" and @checked and @disabled]')
    url = f"/document-steps/{env.row()['form_slug']}/{env.public_id}/paper/fields?token={env.row()['access_token']}"
    response = env.client.post(url, data={"csrf_token": env.csrf, "deklaracja_18_lat": "Tak",
        "wybor_szkolen": ["course-a", "course-b"], "declaration_note": "Dane", "document_action": "save"})
    assert response.status_code == 303
    assert env.row()["selected_trainings"] == before
