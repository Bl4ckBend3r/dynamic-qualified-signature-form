from pathlib import Path


def test_declaration_training_ui_shows_dates_availability_and_no_waitlist():
    template = Path("templates/declaration_form.html").read_text(encoding="utf-8")

    assert "Sprawdź datę szkolenia" in template
    assert "Brak zaplanowanych terminów szkolenia." in template
    assert "available_seats_label" in template
    assert "Brak dostępnych szkoleń." in template
    assert "Dostępne miejsca" not in template  # rendered from service, not hard-coded twice
    assert "fetch(" not in template
    assert "lista rezerwowa" not in template.lower()
    assert "Zapisz na listę rezerwową" not in template


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
    assert ".admin-training-fields" in stylesheet
    assert ".admin-training-table" not in stylesheet
