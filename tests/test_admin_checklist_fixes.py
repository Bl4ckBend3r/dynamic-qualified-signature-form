from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace

from services.admin_form_service import build_definition_from_docx
from services.admin_submission_service import filter_submissions
from services.agreement_context_service import upgrade_training_agreement_total_placeholder
from services.form_option_service import option_label, option_value
from routes.admin.mail_settings import _smtp_error_message


def _docx_with_text(*paragraphs: str) -> bytes:
    body = "".join(
        f'<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>' for paragraph in paragraphs
    )
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body>{body}</w:body></w:document>",
        )
    return content.getvalue()


def test_docx_import_detects_placeholders_and_label_lines():
    definition = build_definition_from_docx(
        _docx_with_text("{{ email }}", "Imię i nazwisko: ______"),
        "formularz.docx",
    )

    assert [(field["name"], field["label"]) for field in definition["fields"]] == [
        ("email", "Email"),
        ("imie_i_nazwisko", "Imię i nazwisko"),
    ]


def test_docx_import_explains_how_to_mark_fields():
    try:
        build_definition_from_docx(_docx_with_text("Zwykły dokument bez pól"), "szablon.docx")
    except ValueError as exc:
        assert "Nie wykryto pól" in str(exc)
        assert "{{ nazwa_pola }}" in str(exc)
    else:
        raise AssertionError("DOCX without field markers should be rejected")


def test_technical_option_value_is_hidden_behind_label():
    assert option_value("yes|Tak, zgadzam się") == "yes"
    assert option_label("yes|Tak, zgadzam się") == "Tak, zgadzam się"
    assert option_value({"value": "no", "label": "Nie"}) == "no"
    assert option_label({"value": "no", "label": "Nie"}) == "Nie"


def test_date_filters_use_complete_local_days():
    before_local_midnight = SimpleNamespace(
        created_at=datetime(2026, 7, 20, 21, 59, tzinfo=timezone.utc),
        process_status="FORM_SUBMITTED", submission_id="before", email="", nazwisko="",
        officer_decision="", data_json={},
    )
    local_day = SimpleNamespace(
        created_at=datetime(2026, 7, 20, 22, 1, tzinfo=timezone.utc),
        process_status="FORM_SUBMITTED", submission_id="inside", email="", nazwisko="",
        officer_decision="", data_json={},
    )

    result = filter_submissions(
        [before_local_midnight, local_day],
        {"date_from": "2026-07-21", "date_to": "2026-07-21"},
        timezone_name="Europe/Warsaw",
    )

    assert [item.submission_id for item in result] == ["inside"]


def test_agreement_total_upgrade_respects_switch():
    source = "<p>{{ selected_trainings_total_formatted }}</p>"
    assert "all_selected_trainings_total_formatted" in upgrade_training_agreement_total_placeholder(
        source, show_all_trainings_total=True
    )
    assert upgrade_training_agreement_total_placeholder(
        source, show_all_trainings_total=False
    ) == source


def test_smtp_authentication_error_has_polish_actionable_message():
    message = _smtp_error_message(RuntimeError("authentication failed"))

    assert message == (
        "Nie udało się zalogować do serwera SMTP. "
        "Sprawdź użytkownika, hasło i metodę szyfrowania."
    )
