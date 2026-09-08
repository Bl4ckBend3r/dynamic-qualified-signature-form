from pathlib import Path
from types import SimpleNamespace

from flask import render_template
from lxml import html


def test_declaration_template_contains_no_training_picker_ui():
    template = Path("templates/declaration_form.html").read_text(encoding="utf-8")

    assert "Wybór szkoleń" not in template
    assert "Sprawdź datę szkolenia" not in template
    assert "data-training-selection" not in template
    assert "data-training-total" not in template
    assert 'field.type == "training_selection"' not in template


def test_public_training_picker_uses_full_training_cards():
    template = Path("templates/training_selection.html").read_text(encoding="utf-8") + Path("templates/partials/training_picker.html").read_text(encoding="utf-8")
    script = Path("static/js/training_selection.js").read_text(encoding="utf-8")

    assert "training-card-list" in template
    assert "training-card__price" in template
    assert "Terminy i lokalizacja" in template
    assert "Dostępne" in template
    assert "Zajęte" in template
    assert "Limit" in template
    assert "Komentarz administratora" in template
    assert "Mało miejsc" in template
    assert "Brak terminów" in template
    assert "Podpisana umowa wgrana. To szkolenie zostało zablokowane" in template
    assert "Zapisz wybór szkoleń" in template
    assert "training_selection.css" in template
    assert "training_selection.js" in template
    assert "data-selection-group" in template
    assert "data-training-base-disabled" in template
    assert "updateSelectionGroups" in script
    assert 'trainingBaseDisabled === "true" || blockedByGroup' in script
    assert 'data-selection-group="{{ training.selection_group or \'\' }}"' in template


def test_public_agreement_cards_render_actions_from_per_training_state():
    template = Path("templates/documents_to_sign.html").read_text(encoding="utf-8")

    assert "{{ agreement.state_title }}" in template
    assert "{{ agreement.state_description }}" in template
    assert "result.uploadable_training_agreements" in template
    assert "agreement.beneficiary_uploaded" in template
    assert "Pobierz wygenerowaną umowę PDF" in template
    assert "Pobierz wgraną podpisaną umowę" in template
    assert "Pobierz finalną umowę podpisaną przez urząd" in template
    assert "Możesz wygenerować umowę, podpisać ją i wgrać podpisany plik PDF." not in template


def test_training_picker_styles_are_wide_and_responsive():
    stylesheet = Path("static/css/training_selection.css").read_text(encoding="utf-8")

    assert ".training-card-list" in stylesheet
    assert "grid-template-columns: minmax(0, 1fr)" in stylesheet
    assert ".training-picker__submit" in stylesheet
    assert "width: auto" in stylesheet
    assert "@media (max-width: 900px)" in stylesheet
    assert "@media (max-width: 640px)" in stylesheet
    assert "width: 100%" in stylesheet


def test_attendance_confirmation_uses_public_design_system_and_mobile_layout():
    template = Path("templates/training_attendance_public.html").read_text(encoding="utf-8")
    stylesheet = Path("static/css/style.css").read_text(encoding="utf-8")

    assert 'class="attendance-confirmation__card card"' in template
    assert '<button class="btn-primary" type="submit">Potwierdź obecność</button>' in template
    assert "attendance-confirmation__details" in template
    assert "Obecność została potwierdzona" in template
    assert "Link jest nieprawidłowy lub wygasł." in template
    assert "Stopka strony —" not in template
    assert "<button type=\"submit\">Potwierdź obecność</button>" not in template
    assert "width: min(100%, 640px)" in stylesheet
    assert ".attendance-confirmation__actions .btn-primary" in stylesheet
    assert ".btn-primary:focus-visible" in stylesheet
    assert "@media (max-width: 640px)" in stylesheet


def test_public_training_test_renders_accessible_option_rows(app):
    survey = SimpleNamespace(
        name="Test PRE — Bezpieczeństwo informacji",
        description="Wybierz poprawne odpowiedzi.",
        survey_type="pre_test",
        attempt_policy="single_attempt",
    )
    questions = [
        SimpleNamespace(id=1, text="Jedna odpowiedź", question_type="single_choice", options_json=["Alfa", "Beta"], required=True),
        SimpleNamespace(id=2, text="Wiele odpowiedzi", question_type="multiple_choice", options_json=["Ciekły", "Stały"], required=True),
        SimpleNamespace(id=3, text="Prawda czy fałsz", question_type="true_false", options_json=["true", "false"], required=True),
    ]
    with app.test_request_context("/training-survey/test-token"):
        rendered = render_template(
            "training_survey_public.html",
            valid=True,
            completed=False,
            survey=survey,
            questions=questions,
        )

    document = html.fromstring(rendered)
    question_cards = document.xpath('//article[contains(concat(" ", normalize-space(@class), " "), " training-test-question ")]')
    assert len(question_cards) == 3
    assert all(len(card.xpath("./fieldset/legend")) == 1 for card in question_cards)
    assert all(len(card.xpath('.//span[contains(@class, "training-test-question__number")]')) == 1 for card in question_cards)
    assert all(len(card.xpath('.//span[contains(@class, "training-test-question__title")]')) == 1 for card in question_cards)
    option_labels = document.xpath('//label[contains(concat(" ", normalize-space(@class), " "), " training-test-option ")]')
    assert len(option_labels) == 6
    assert all(len(label.xpath("./input")) == 1 for label in option_labels)
    assert all(len(label.xpath('./span[contains(@class, "training-test-option__text")]')) == 1 for label in option_labels)
    assert len(document.xpath('//input[@type="radio" and @name="question_1"]/parent::label')) == 2
    assert len(document.xpath('//input[@type="checkbox" and @name="question_2"]/parent::label')) == 2
    assert len(document.xpath('//input[@type="radio" and @name="question_3"]/parent::label')) == 2
    assert document.xpath('//input[@name="question_1" and @value="Alfa"]')
    assert document.xpath('//input[@name="question_2" and @value="Ciekły"]')
    assert "Prawda" in rendered and "Fałsz" in rendered
    assert ">>>>" not in rendered
    assert document.xpath('//input[@name="csrf_token" and @type="hidden"]')
    assert document.xpath('//button[@type="submit" and contains(@class, "btn-primary") and normalize-space()="Zakończ test"]')
    assert document.xpath("//fieldset/legend")
    assert len(document.xpath('//fieldset[@aria-required="true" and @aria-describedby]')) == 3
    assert len(document.xpath('//p[@data-question-error and @aria-live="polite"]')) == 3
    assert document.xpath('//dialog[@data-training-test-dialog]')


def test_public_training_test_styles_cover_selection_focus_and_mobile():
    template = Path("templates/training_survey_public.html").read_text(encoding="utf-8")
    stylesheet = Path("static/css/training_test.css").read_text(encoding="utf-8")

    assert "training_test.css" in template
    assert "data-training-test-progress" in template
    assert "Nie odpowiedziano na pytania" in template
    assert "data-single-attempt" in template
    assert "width: min(100%, 900px)" in stylesheet
    assert ".training-test__questions { display: grid; gap: 24px; }" in stylesheet
    assert ".training-test-question__fieldset" in stylesheet
    assert "padding: 26px" in stylesheet
    assert "border: 0" in stylesheet
    assert ".training-test-option:has(.training-test-option__control:checked)" in stylesheet
    assert ".training-test-option:has(.training-test-option__control:focus-visible)" in stylesheet
    assert "width: 1.15rem" in stylesheet
    assert "flex: 0 0 auto" in stylesheet
    assert "min-height: 44px" in stylesheet
    assert "overflow-wrap: anywhere" in stylesheet
    assert "@media (max-width: 640px)" in stylesheet
    assert "padding: 18px" in stylesheet
    assert "font-size: 1.2rem" in stylesheet
    assert ".training-test__actions .btn-primary" in stylesheet
    assert "!important" not in stylesheet


def test_training_management_suppresses_only_local_required_notes():
    template = Path("templates/admin/trainings/detail.html").read_text(encoding="utf-8")
    admin_base = Path("templates/admin/base.html").read_text(encoding="utf-8")

    assert template.count('data-required-note="false"') == 2
    assert 'class="training-cancel-form"' in template
    assert 'name="reason" required' in template
    assert 'class="training-attendance-form"' in template
    assert 'Nazwa <span class="required-marker" aria-hidden="true">*</span>' in template
    assert 'Data <span class="required-marker" aria-hidden="true">*</span>' in template
    assert 'name="name" required' in template
    assert 'name="session_date" required' in template
    assert "Utwórz sesję" in template
    assert "Pola oznaczone * są obowiązkowe." not in template
    assert 'form.dataset.requiredNote !== "false"' in admin_base
    assert 'note.className = "admin-required-note"' in admin_base


def test_training_picker_script_updates_and_enforces_limit():
    script = Path("static/js/training_selection.js").read_text(encoding="utf-8")
    template = Path("templates/training_selection.html").read_text(encoding="utf-8") + Path("templates/partials/training_picker.html").read_text(encoding="utf-8")

    assert "data-training-price" in script
    assert "used > limit" in script
    assert "submitButton.disabled = exceeded" in script
    assert 'useGrouping: "always"' in script
    assert "Pozostało" not in script  # label stays in HTML, amount is updated in JS
    assert "Wybrane szkolenia przekraczają limit" in script
    assert "5000" not in script
    assert "5000" not in template
    assert "Excel" not in script
    assert "Excel" not in template


def test_status_training_picker_is_collapsed_with_accessible_toggle():
    template = Path("templates/documents_to_sign.html").read_text(encoding="utf-8")
    script = Path("static/js/training_selection.js").read_text(encoding="utf-8")

    assert 'aria-expanded="false"' in template
    assert 'aria-controls="public-training-picker"' in template
    assert 'id="public-training-picker"' in template and " hidden " in template
    assert "Wybierz szkolenia" in template
    assert "Ukryj wybór szkoleń" in script
    assert 'toggle.setAttribute("aria-expanded"' in script


def test_admin_training_editor_contains_only_selection_stage_settings():
    template = Path("templates/admin/forms/edit.html").read_text(encoding="utf-8")
    stylesheet = Path("static/css/admin.css").read_text(encoding="utf-8")

    assert 'name="training_item_code"' not in template
    assert "RRRR-MM-DD|" not in template
    assert "Konfiguracja etapu wyboru szkoleń" in template
    assert "Dostępne szkolenia" not in template
    assert "+ Dodaj szkolenie" not in template
    assert "wersjonowany katalog formularza" not in template
    assert "Przejdź do zarządzania szkoleniami" in template
    assert "Dotyczy osobnego etapu wyboru szkoleń" in template
    for name in (
        "training_selection_enabled", "training_selection_name", "training_selection_label",
        "training_selection_max_total", "training_selection_currency", "training_selection_required",
    ):
        assert f'name="{name}"' in template
    for name in (
        "training_item_id", "training_item_name", "training_item_price",
        "training_item_currency", "training_item_capacity", "training_item_active",
        "training_item_sort_order",
    ):
        assert f'name="{name}"' not in template
    assert ".admin-training-fields" in stylesheet


def test_admin_training_list_is_an_accessible_inline_editor():
    template = Path("templates/admin/trainings/index.html").read_text(encoding="utf-8")

    assert "data-training-toggle" in template
    assert 'aria-expanded="' in template
    assert 'aria-controls="{{ panel_id }}"' in template
    assert "data-training-panel" in template
    assert "admin.training_catalog_update_inline" in template
    assert "admin.training_catalog_create_inline" in template
    assert "admin.training_management_detail" in template
    assert "+ Dodaj szkolenie" in template
    assert 'name="form_version_id"' not in template
    assert 'name="training_item_id"' in template
    assert "data-add-inline-training-date" in template
    assert "data-remove-inline-training-date" in template
    assert "onclick=" not in template
    assert 'closest("[data-training-toggle]")' in template
    assert 'name="training_item_selection_group_choice"' in template
    assert 'name="training_item_selection_group_new"' in template
    assert "Brak powiązania" in template
    assert "+ Dodaj nową grupę" in template
    assert "data-selection-group-choice" in template
    assert "newInput.required = creatingGroup" in template


def test_document_training_picker_uses_the_same_group_locking_script():
    template = Path("templates/form_page.html").read_text(encoding="utf-8")

    assert "data-selection-group" in template
    assert "data-training-base-disabled" in template
    assert "js/training_selection.js" in template


def test_admin_training_detail_has_operational_cards_and_one_initial_survey_question():
    template = Path("templates/admin/trainings/detail.html").read_text(encoding="utf-8")

    for hook in (
        "training-kpi-grid",
        "training-filter-grid",
        "training-participants-table",
        'id="messages"',
        'id="attendance"',
        'id="surveys"',
        'id="history"',
    ):
        assert hook in template
    assert "data-add-question" in template
    assert "data-training-question-template" in template
    assert "range(4)" not in template


def test_admin_training_module_styles_cover_desktop_and_mobile_layouts():
    stylesheet = Path("static/css/admin.css").read_text(encoding="utf-8")

    for selector in (
        ".training-list",
        ".training-row",
        ".training-edit-form",
        ".training-kpi-grid",
        ".training-filter-grid",
        ".training-question-card",
    ):
        assert selector in stylesheet
    assert "@media (max-width: 1100px)" in stylesheet
    assert "@media (max-width: 768px)" in stylesheet
