from pathlib import Path


def test_declaration_template_contains_no_training_picker_ui():
    template = Path("templates/declaration_form.html").read_text(encoding="utf-8")

    assert "Wybór szkoleń" not in template
    assert "Sprawdź datę szkolenia" not in template
    assert "data-training-selection" not in template
    assert "data-training-total" not in template
    assert 'field.type == "training_selection"' not in template


def test_public_training_picker_uses_full_training_cards():
    template = Path("templates/training_selection.html").read_text(encoding="utf-8")

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


def test_training_picker_styles_are_wide_and_responsive():
    stylesheet = Path("static/css/training_selection.css").read_text(encoding="utf-8")

    assert ".training-card-list" in stylesheet
    assert "grid-template-columns: minmax(0, 1fr)" in stylesheet
    assert ".training-picker__submit" in stylesheet
    assert "width: auto" in stylesheet
    assert "@media (max-width: 900px)" in stylesheet
    assert "@media (max-width: 640px)" in stylesheet
    assert "width: 100%" in stylesheet


def test_training_picker_script_updates_and_enforces_limit():
    script = Path("static/js/training_selection.js").read_text(encoding="utf-8")
    template = Path("templates/training_selection.html").read_text(encoding="utf-8")

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


def test_admin_training_ui_uses_sections_and_readable_date_fields():
    template = Path("templates/admin/forms/edit.html").read_text(encoding="utf-8")
    stylesheet = Path("static/css/admin.css").read_text(encoding="utf-8")

    assert 'name="training_item_code"' not in template
    assert "RRRR-MM-DD|" not in template
    assert 'data-training-card' in template
    assert 'type="date" name="training_date_start_date"' in template
    assert 'type="time" name="training_date_start_time"' in template
    assert "Dodaj termin" in template
    assert "Usuń termin" in template
    assert "Te szkolenia nie są częścią deklaracji." in template
    assert "Te szkolenia są źródłem danych dla publicznego etapu wyboru szkoleń" in template
    assert "Dotyczy osobnego etapu wyboru szkoleń" in template
    assert 'name="training_item_admin_comment"' in template
    assert "data-training-removals" in template
    assert "training_removed_reason" in template
    assert 'name="training_item_change_reason"' in template
    assert "Powód dezaktywacji" in template
    assert "Powód usunięcia lub archiwizacji jest wymagany" in template
    assert "Archiwalne" in template
    assert ".admin-training-fields" in stylesheet
    assert ".admin-training-table" not in stylesheet
