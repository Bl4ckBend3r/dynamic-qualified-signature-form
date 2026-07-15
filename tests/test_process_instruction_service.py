from services.process_instruction_service import build_process_instruction_view, normalize_instruction_config


def config(*stages, title="Moja instrukcja", description="Opis ogólny"):
    return {"title": title, "description": description, "stages": list(stages)}


def stage(label, statuses, *, next_action="", description="", final=False, rejected=False):
    return {
        "label": label,
        "status_codes": statuses,
        "next_action": next_action,
        "description": description,
        "final": final,
        "rejected": rejected,
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


def test_instruction_content_is_plain_text_and_control_bytes_are_removed():
    normalized = normalize_instruction_config(
        config(stage("<b>Etap</b>\x00", ["FORM_SUBMITTED"], next_action="<script>alert(1)</script>"))
    )

    assert normalized["stages"][0]["label"] == "<b>Etap</b>"
    assert "\x00" not in normalized["stages"][0]["label"]
    assert normalized["stages"][0]["next_action"] == "<script>alert(1)</script>"
