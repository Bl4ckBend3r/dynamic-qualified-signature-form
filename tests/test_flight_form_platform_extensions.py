from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from form_loader import extract_submission_data, load_form_definition, validate_submission
from models import Base, Form, FormSubmission, FormVersion
from services.decision_definition_service import DecisionDefinitionError, DecisionDefinitionService
from services.documents.declaration_template_context_service import build_declaration_render_context
from services.submission_service import SubmissionService
from services.workflow_service import WorkflowService, WorkflowTransitionError
from validators.form_config_validator import FormConfigValidator


LOTY_PATH = Path("examples/forms/loty.json")


def _loty():
    return load_form_definition(LOTY_PATH)


def _participant(record_uuid, **overrides):
    value = {
        "record_uuid": record_uuid,
        "id": record_uuid,
        "imie": "Jan",
        "nazwisko": "Kowalski",
        "stanowisko_departament_wydzial": "DIT",
        "stanowisko_departament_wydzial_jednostka": "Spółka",
        "ieg_waw_dzien": "2026-09-10",
        "ieg_waw_godzina": "08:00",
        "waw_ieg_dzien": "2026-09-11",
        "waw_ieg_godzina": "18:00",
        "telefon": "500600700",
        "email": "jan@example.org",
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize(
    ("applicant_type", "missing_field", "expected_error"),
    [
        ("urzednik", "stanowisko_departament_wydzial", True),
        ("ambasador_wojewodztwa", "stanowisko_departament_wydzial", False),
        ("jednostka_lub_spolka", "stanowisko_departament_wydzial_jednostka", True),
    ],
)
def test_root_scope_visibility_and_required_are_validated_for_every_record(applicant_type, missing_field, expected_error):
    ids = [str(uuid4()), str(uuid4())]
    records = [_participant(value, **{missing_field: ""}) for value in ids]
    data = {
        "typ_wnioskodawcy": applicant_type,
        "miejscowosc": "Zielona Góra",
        "data_wniosku": "2026-09-01",
        "rodzaj_biletu": "nieodpłatnych",
        "podrozni": records,
        "cel_podrozy_uzasadnienie": "Spotkanie",
        "dane_do_faktury": "",
    }
    errors = validate_submission(_loty(), data)
    keys = {f"podrozni.{record_uuid}.{missing_field}" for record_uuid in ids}
    assert bool(keys & errors.keys()) is expected_error


def test_every_repeatable_email_is_validated():
    first, second = str(uuid4()), str(uuid4())
    data = {
        "typ_wnioskodawcy": "ambasador_wojewodztwa", "miejscowosc": "Zielona Góra",
        "data_wniosku": "2026-09-01", "rodzaj_biletu": "nieodpłatnych",
        "podrozni": [_participant(first), _participant(second, email="bad")],
        "cel_podrozy_uzasadnienie": "Spotkanie", "dane_do_faktury": "",
    }
    assert f"podrozni.{second}.email" in validate_submission(_loty(), data)


def test_record_uuid_is_canonical_and_applicant_identity_is_stable():
    first, second = str(uuid4()), str(uuid4())
    values = extract_submission_data(_loty(), {"podrozni": str([{"id": first}, {"record_uuid": second}]).replace("'", '"')})
    assert [(item["record_uuid"], item["id"]) for item in values["podrozni"]] == [(first, first), (second, second)]
    metadata = SubmissionService._repeatable_identity_metadata(_loty(), values)
    assert metadata["_applicant_record_id"] == first
    existing = {"data_json": metadata}
    assert SubmissionService._validate_preserved_applicants(_loty(), {"podrozni": list(reversed(values["podrozni"]))}, existing) == {}
    assert "podrozni" in SubmissionService._validate_preserved_applicants(_loty(), {"podrozni": [values["podrozni"][1]]}, existing)


def test_conditional_workflow_branches_and_fails_closed():
    service = WorkflowService()
    config = _loty()
    expected = {
        "urzednik": "urzednik_review",
        "ambasador_wojewodztwa": "ambasador_review",
        "jednostka_lub_spolka": "jednostka_spolka_review",
    }
    for value, target in expected.items():
        assert service.resolve_next_step(config, "declaration_signed", submission_data={"typ_wnioskodawcy": value}) == target
    with pytest.raises(WorkflowTransitionError):
        service.resolve_next_step(config, "declaration_signed", submission_data={"typ_wnioskodawcy": "xyz"})


def test_item_decision_snapshot_reason_and_all_completion():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    form = Form(slug="loty-test", name="Loty", title="Loty")
    db.add(form)
    db.flush()
    definition = _loty()
    # Narrow this test to one of the independently configured DIT stages.
    version = FormVersion(form_id=form.id, version_major=1, version_minor=0, version_label="1.0", status="published", definition_json=definition)
    db.add(version)
    db.flush()
    first, second, third = (str(uuid4()) for _ in range(3))
    submission = FormSubmission(
        submission_id=str(uuid4()), form_slug=form.slug, form_version_id=version.id,
        workflow_step="dit_decision_urzednik", workflow_stage="dit_decision_urzednik",
        process_status="WAITING_FOR_OFFICER_DECISION",
        data_json={"podrozni": [_participant(first), _participant(second, imie="Anna", email="anna@example.org"), _participant(third)]},
    )
    db.add(submission)
    db.flush()
    actor = SimpleNamespace(id=None, email="dit@example.org", role="admin")
    service = DecisionDefinitionService()
    with pytest.raises(DecisionDefinitionError):
        service.decide_item(db, submission, group_key="podrozni", item_id=second, decision_code="brak_biletow_pula_wykorzystana", comment="", actor=actor)
    decision = service.decide_item(db, submission, group_key="podrozni", item_id=second, decision_code="brak_biletow_pula_wykorzystana", comment="Brak uzasadnienia podróży", actor=actor)
    assert decision.participant_snapshot_json["record_uuid"] == second
    assert decision.participant_snapshot_json["email"] == "anna@example.org"
    service.decide_item(db, submission, group_key="podrozni", item_id=first, decision_code="bilet_bezplatny", comment="", actor=actor)
    assert service.completion_state(db, submission, "podrozni")["complete"] is False
    service.decide_item(db, submission, group_key="podrozni", item_id=third, decision_code="bilet_bezplatny", comment="", actor=actor)
    state = service.completion_state(db, submission, "podrozni")
    assert state["complete"] is True
    assert state["aggregate"] == "PARTIALLY_ACCEPTED"


def test_declaration_resolves_signer_by_applicant_uuid_after_reorder():
    first, second = str(uuid4()), str(uuid4())
    context = build_declaration_render_context(
        {"data_json": {"podrozni": [_participant(second, imie="Anna"), _participant(first)], "_applicant_record_id": first}},
        form_definition=_loty(), fields=_loty()["fields"],
    )
    assert context["applicant"]["record_uuid"] == first
    assert context["applicant"]["imie"] == "Jan"
    assert context["podrozni"][0]["is_applicant"] is False


def test_final_loty_json_passes_real_parser_and_validator():
    definition = _loty()
    assert FormConfigValidator(skip_template_check=True).validate(definition) == []


def test_flight_ticket_options_in_draft_use_exact_catalog_choices():
    definition = _loty()
    assert FormConfigValidator(skip_template_check=True).validate(definition) == []
    expected = [
        ('bilet_bezplatny', 'Bilet bezpłatny', 'positive', False),
        ('bilet_ze_znizka', 'Bilet ze zniżką', 'positive', False),
        ('brak_biletow_pula_wykorzystana', 'Brak biletów — pula wykorzystana', 'negative', True),
    ]
    group = next(f for f in definition['fields'] if f['type'] == 'repeatable_group')
    service = DecisionDefinitionService()
    draft = SimpleNamespace(status='draft', definition_json=definition)
    for step in group['decision_completion']['step_ids']:
        submission = SimpleNamespace(form_version=draft, workflow_stage=step, workflow_step=step)
        options = service.available_for_submission(submission, scope='item')
        assert [(o['code'], o['label'], o['semantic_category'], o['require_reason']) for o in options] == expected
        # The sample uses the same snapshots as the catalog assignment API.
        for option in options:
            catalog_entry = SimpleNamespace(id=123, is_active=True, code=option['code'], label=option['label'],
                description='', semantic_category=option['semantic_category'], sort_order=option['sort_order'])
            service.assign_to_draft(draft, catalog_entry, step_id=step, target_step=option['target_step'],
                scope='item', require_reason=option['require_reason'])
        assert [o['code'] for o in service.available_for_submission(submission, scope='item')] == [row[0] for row in expected]
