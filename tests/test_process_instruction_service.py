from services.process_instruction_service import (
    build_process_instruction_view,
    normalize_instruction_config,
    reconcile_instruction_config,
)


def config(*stages, title="Moja instrukcja", description="Opis ogólny"):
    return {"title": title, "description": description, "stages": list(stages)}


def stage(label, statuses, *, next_action="", description="", final=False, rejected=False,
          show_only_when_current=False):
    return {
        "label": label,
        "status_codes": statuses,
        "next_action": next_action,
        "description": description,
        "final": final,
        "rejected": rejected,
        "show_only_when_current": show_only_when_current,
    }


def test_form_without_instruction_has_no_hardcoded_stages():
    view = build_process_instruction_view("FORM_SUBMITTED")

    assert view["instruction"]["has_instruction"] is False
    assert view["instruction_steps"] == []
    assert view["next_action"] == ""


def test_legacy_general_instruction_works_without_stages():
    view = build_process_instruction_view("OFFICER_ACCEPTED", legacy_description="Dalsze informacje.")

    assert view["instruction"]["description"] == "Dalsze informacje."
    assert view["instruction"]["has_instruction"] is True
    assert view["current_step"] is None
    assert view["instruction_steps"] == []


def test_title_without_description_or_stages_does_not_create_empty_window():
    view = build_process_instruction_view("FORM_SUBMITTED", instruction_config={"title": "Sam tytuł"})

    assert view["instruction"]["has_instruction"] is False
    assert view["instruction_steps"] == []


def test_one_stage_maps_multiple_raw_and_normalized_statuses():
    instruction = config(
        stage(
            "Deklaracja niestandardowa",
            ["OFFICER_ACCEPTED", "REVIEW_ACCEPTED"],
            next_action="Wypełnij niestandardowy dokument.",
            description="Opis aktualnego etapu.",
        )
    )

    raw = build_process_instruction_view("OFFICER_ACCEPTED", instruction_config=instruction)
    normalized = build_process_instruction_view("REVIEW_ACCEPTED", instruction_config=instruction)

    assert raw["current_step_label"] == "Deklaracja niestandardowa"
    assert normalized["current_step_label"] == "Deklaracja niestandardowa"
    assert raw["next_action"] == "Wypełnij niestandardowy dokument."
    assert raw["instruction"]["current_stage_description"] == "Opis aktualnego etapu."


def test_many_stages_follow_configured_order_and_mark_previous_completed():
    instruction = config(
        stage("Start własny", ["FORM_SUBMITTED"]),
        stage("Kontakt telefoniczny", ["WAITING_FOR_OFFICER_DECISION"]),
        stage("Własny finał", ["PROCESS_COMPLETED"], final=True),
    )

    view = build_process_instruction_view("WAITING_FOR_OFFICER_DECISION", instruction_config=instruction)

    assert [item["label"] for item in view["instruction_steps"]] == [
        "Start własny",
        "Kontakt telefoniczny",
        "Własny finał",
    ]
    assert view["instruction_steps"][0]["completed"] is True
    assert view["instruction_steps"][1]["current"] is True
    assert view["instruction_steps"][2]["completed"] is False


def test_unassigned_status_uses_safe_fallback_without_error():
    view = build_process_instruction_view(
        "CUSTOM_STATUS",
        instruction_config=config(stage("Szkic urzędnika", ["DRAFT"])),
    )

    fallback = view["instruction_steps"][-1]
    assert fallback["current"] is True
    assert fallback["fallback"] is True
    assert "CUSTOM_STATUS" in fallback["label"]
    assert view["next_action"] == ""


def test_instruction_content_allows_safe_html_and_removes_dangerous_markup():
    normalized = normalize_instruction_config(
        config(
            stage(
                "<b>Etap</b>\x00",
                ["FORM_SUBMITTED"],
                description='<p onclick="alert(1)"><strong>Ważne</strong><img src=x></p>',
                next_action='<script>alert(1)</script><a href="javascript:alert(1)">Niebezpieczny</a><a href="https://example.com" style="color:red">Przejdź</a>',
            )
        )
    )

    assert normalized["stages"][0]["label"] == "<b>Etap</b>"
    assert "\x00" not in normalized["stages"][0]["label"]
    assert normalized["stages"][0]["description"] == "<p><strong>Ważne</strong></p>"
    assert "javascript:" not in normalized["stages"][0]["next_action"]
    assert normalized["stages"][0]["next_action"].endswith(
        '<a href="https://example.com" target="_blank" rel="noopener noreferrer">Przejdź</a>'
    )


def test_inactive_instruction_is_preserved_but_not_exposed_publicly():
    reconciled = reconcile_instruction_config(
        config(
            {"key": "submission", **stage("Wniosek", ["FORM_SUBMITTED"])},
            {"key": "removed", **stage("Usunięty etap", ["REMOVED_STATUS"], next_action="Nie pokazuj")},
        ),
        {"steps": [{"id": "submission", "status": "FORM_SUBMITTED"}]},
    )

    inactive = next(item for item in reconciled["stages"] if item["key"] == "removed")
    assert inactive["active"] is False
    assert inactive["inactive_reason"] == "Ten etap nie występuje już w workflow."
    view = build_process_instruction_view("REMOVED_STATUS", instruction_config=reconciled)
    assert all(item["key"] != "removed" for item in view["instruction_steps"])


def test_conditional_stages_are_visible_only_while_current_and_renumbered():
    instruction = config(
        stage("Wniosek", ["FORM_SUBMITTED"]),
        stage("Korekta", ["WAITING_FOR_CORRECTION"],
              description="Popraw dane.", next_action="Wyślij korektę.", show_only_when_current=True),
        stage("Decyzja", ["WAITING_FOR_OFFICER_DECISION"]),
        stage("Odrzucony", ["OFFICER_REJECTED"], rejected=True, show_only_when_current=True),
    )
    normalized = normalize_instruction_config(instruction)
    assert [item["show_only_when_current"] for item in normalized["stages"]] == [False, True, False, True]
    assert normalize_instruction_config(config({"label": "Starszy etap", "status_codes": ["DRAFT"]}))["stages"][0]["show_only_when_current"] is False

    before = build_process_instruction_view("FORM_SUBMITTED", instruction_config=instruction)
    correction = build_process_instruction_view("WAITING_FOR_CORRECTION", instruction_config=instruction)
    after = build_process_instruction_view("WAITING_FOR_OFFICER_DECISION", instruction_config=instruction)
    rejected = build_process_instruction_view("OFFICER_REJECTED", instruction_config=instruction)

    assert [item["label"] for item in before["instruction_steps"]] == ["Wniosek", "Decyzja"]
    assert before["next_stage_label"] is None
    assert before["instruction"]["next_stage_key"] is None
    assert [item["label"] for item in correction["instruction_steps"]] == ["Wniosek", "Korekta", "Decyzja"]
    assert correction["current_step_label"] == "Korekta"
    assert correction["instruction"]["current_stage_description"] == "Popraw dane."
    assert correction["next_action"] == "Wyślij korektę."
    assert [item["label"] for item in after["instruction_steps"]] == ["Wniosek", "Decyzja"]
    assert [item["label"] for item in rejected["instruction_steps"]] == ["Wniosek", "Decyzja", "Odrzucony"]
    assert [item["sort_order"] for item in rejected["instruction_steps"]] == [1, 2, 3]


def test_unmatched_status_keeps_fallback_without_revealing_conditional_stage():
    view = build_process_instruction_view("UNKNOWN", instruction_config=config(
        stage("Start", ["FORM_SUBMITTED"]),
        stage("Przyszła korekta", ["WAITING_FOR_CORRECTION"], show_only_when_current=True),
    ))
    assert view["instruction_steps"][0]["label"] == "Start"
    assert "UNKNOWN" in view["instruction_steps"][1]["label"]
    assert view["instruction_steps"][-1]["fallback"] is True
    assert view["next_stage_label"] is None


def test_explicit_workflow_filters_conditional_stages_and_next_stage():
    workflow = {"flow_mode": "explicit", "steps": [
        {"id": "start", "user_label": "Wniosek", "next": "correction"},
        {"id": "correction", "user_label": "Korekta", "next": "rejected",
         "description": "Popraw dane.", "next_action": "Wyślij korektę.", "show_only_when_current": True},
        {"id": "rejected", "user_label": "Odrzucony", "show_only_when_current": True},
    ]}
    before = build_process_instruction_view("FORM_SUBMITTED", workflow=workflow, step_id="start")
    correction = build_process_instruction_view("WAITING_FOR_CORRECTION", workflow=workflow, step_id="correction")
    rejected = build_process_instruction_view("OFFICER_REJECTED", workflow=workflow, step_id="rejected")

    assert [item["label"] for item in before["instruction_steps"]] == ["Wniosek"]
    assert before["instruction"]["next_stage_key"] is None
    assert before["next_stage_label"] is None
    assert [item["label"] for item in correction["instruction_steps"]] == ["Wniosek", "Korekta"]
    assert correction["current_step_label"] == "Korekta"
    assert correction["instruction"]["current_stage_description"] == "Popraw dane."
    assert correction["next_action"] == "Wyślij korektę."
    assert correction["next_stage_label"] is None
    assert [item["label"] for item in rejected["instruction_steps"]] == ["Wniosek", "Odrzucony"]
    assert [item["sort_order"] for item in rejected["instruction_steps"]] == [1, 2]

    config_only = config({"key": "correction", **stage("Korekta", ["WAITING_FOR_CORRECTION"],
                                                     show_only_when_current=True)})
    legacy_workflow = {**workflow, "steps": [{key: value for key, value in item.items()
                                               if key != "show_only_when_current"} for item in workflow["steps"]]}
    legacy_before = build_process_instruction_view("FORM_SUBMITTED", workflow=legacy_workflow,
                                                   step_id="start", instruction_config=config_only)
    assert [item["label"] for item in legacy_before["instruction_steps"]] == ["Wniosek", "Odrzucony"]
