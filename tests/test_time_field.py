from form_loader import validate_submission

def test_time_field_accepts_valid_time():
    form_definition = {
        "title": "Test",
        "fields": [
            {
                "type": "time",
                "name": "godzina",
                "label": "Godzina",
                "required": True,
            }
        ],
    }

    errors = validate_submission(
        form_definition,
        {"godzina": "08:35"},
    )

    assert errors == {}

def test_time_field_rejects_invalid_time():
    form_definition = {
        "title": "Test",
        "fields": [
            {
                "type": "time",
                "name": "godzina",
                "label": "Godzina",
                "required": True,
            }
        ],
    }

    errors = validate_submission(
        form_definition,
        {"godzina": "25:70"},
    )

    assert "godzina" in errors