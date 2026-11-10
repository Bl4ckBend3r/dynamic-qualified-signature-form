"""Canonical IDs and source policies for mail produced by the platform.

Form templates may override only the types whose policy permits it. A dynamic
message supplies its content at the event call site; SMTP diagnostics are
technical mail and are deliberately outside the system template catalog.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from types import SimpleNamespace


@dataclass(frozen=True)
class SystemMailType:
    id: str
    name: str
    source_policy: str
    recipient: str
    trigger: str
    form_match: str = "type"
    form_order: str = "latest"
    form_trigger_status: str = ""
    show_process_status: bool = True


SYSTEM_MAIL_TYPES = (
    SystemMailType(
        "submission_received", "Potwierdzenie otrzymania zgłoszenia", "platform_required",
        "adres zgłoszenia lub skonfigurowana grupa", "złożenie lub ponowienie zgłoszenia",
    ),
    SystemMailType(
        "auto_rejected_by_condition", "Odrzucenie zgłoszenia", "form_platform_fallback",
        "adres zgłoszenia lub skonfigurowana grupa", "automatyczna kwalifikacja",
    ),
    SystemMailType(
        "returned_for_correction", "Zgłoszenie wysłane do poprawy", "form_platform_fallback",
        "adres zgłoszenia lub wskazany odbiorca", "zwrot do korekty",
    ),
    SystemMailType(
        "form_draft_resume", "Link do wersji roboczej formularza", "form_fallback",
        "adres wersji roboczej", "zapis lub ponowienie linku",
        form_match="type_or_trigger", show_process_status=False,
    ),
    SystemMailType(
        "new_submission_admin", "Nowe zgłoszenie", "dynamic",
        "adres administratora w wersji formularza", "zapis nowego zgłoszenia",
    ),
    SystemMailType(
        "document_received", "Powiadomienie o otrzymaniu dokumentu", "dynamic",
        "adres administratora w wersji formularza", "przyjęty upload uczestnika",
    ),
    SystemMailType(
        "stage_rollback", "Cofnięcie etapu zgłoszenia", "form_fallback",
        "adres zgłoszenia", "cofnięcie etapu",
    ),
    SystemMailType(
        "agreement_signed_by_office", "Umowa podpisana przez urząd", "form_fallback",
        "adres zgłoszenia", "decyzja urzędu o umowie",
        form_match="type_or_trigger", form_order="default_then_latest",
        form_trigger_status="AGREEMENT_SIGNED_BY_OFFICE",
    ),
    SystemMailType(
        "agreement_rejected_by_office", "Decyzja dotycząca podpisanej umowy", "form_fallback",
        "adres zgłoszenia", "odrzucenie umowy przez urząd",
        form_match="type_or_trigger", form_order="default_then_latest",
        form_trigger_status="AGREEMENT_SIGNED_BY_OFFICE",
    ),
    SystemMailType(
        "training_attendance_invitation", "Potwierdzenie obecności", "dynamic",
        "adres zgłoszenia dla uczestnika szkolenia", "wysłanie linku obecności",
    ),
    SystemMailType(
        "training_survey_invitation", "Zaproszenie do ankiety szkoleniowej", "dynamic",
        "adres zgłoszenia dla uczestnika szkolenia", "wysłanie linku ankiety",
    ),
)

SYSTEM_MAIL_BY_ID = {item.id: item for item in SYSTEM_MAIL_TYPES}
if len(SYSTEM_MAIL_BY_ID) != len(SYSTEM_MAIL_TYPES):
    raise ValueError("Duplicate system mail type ID")

# Existing form-scoped editor values remain valid. Overlapping IDs above mean
# an optional form override, not another system message.
FORM_MAIL_TEMPLATE_TYPES = (
    "confirmation", "accepted", "rejected", "correction_required",
    "declaration_signed", "agreement_ready", "agreement_signed_by_user",
    "agreement_signed_by_office", "agreement_rejected_by_office",
    "stage_rollback", "auto_rejected_by_condition", "returned_for_correction",
    "custom", "correction_accepted", "form_draft_resume",
    "repeatable_item_decision",
)

FORM_MAIL_TEMPLATE_LABELS = {
    "correction_accepted": "Akceptacja poprawionego wniosku",
    "confirmation": "Złożenie wniosku",
    "accepted": "Akceptacja",
    "rejected": "Odrzucenie",
    "correction_required": "Wysłanie do poprawy",
    "declaration_ready": "Deklaracja gotowa",
    "declaration_signed": "Deklaracja podpisana",
    "agreement_ready": "Umowa gotowa",
    "agreement_signed_by_user": "Umowa podpisana przez beneficjenta",
    "repeatable_item_decision": "Decyzja dla elementu listy",
    "custom": "Wiadomość własna",
    "returned_for_correction": "Wysłanie do poprawy",
    "auto_rejected_by_condition": "Odrzucenie zgłoszenia",
    "agreement_signed_by_office": "Umowa podpisana przez urząd",
    "stage_rollback": "Cofnięcie etapu",
}

# These are event IDs for the same confirmation, not additional template IDs.
CONFIRMATION_EVENTS = ("submission_created", "submission_received")
TECHNICAL_MAIL_TYPES = ("smtp_test",)


DEFAULT_TEMPLATE_ROOT = Path(__file__).resolve().parent.parent / "system_mail_templates"
DEFAULT_TEMPLATE_FILES = ("subject.txt", "body.html", "body.txt")


class SystemMailTemplateError(RuntimeError):
    """A registered default template is missing or cannot be read safely."""


@dataclass(frozen=True)
class MailVariable:
    name: str
    description: str
    example: str = ""
    category: str = "Zgłoszenie"
    scope: str = "Wspólna"

    @property
    def syntax(self) -> str:
        return "{{ " + self.name + " }}"


# Public, stable keys produced by the mail context builders and event call
# sites.  Raw submission columns, access tokens and arbitrary form fields are
# intentionally excluded from this administrator catalog.
_COMMON_VARIABLES = (
    MailVariable("submission_id", "Publiczny numer zgłoszenia.", "ZGŁ-2026-001"),
    MailVariable("public_submission_id", "Publiczny numer zgłoszenia (alias).", "ZGŁ-2026-001"),
    MailVariable("form_name", "Nazwa formularza.", "Wniosek o szkolenie", "Formularz"),
    MailVariable("form_title", "Tytuł formularza.", "Wniosek o szkolenie", "Formularz"),
    MailVariable("form_slug", "Identyfikator formularza w adresie.", "wniosek-szkolenie", "Formularz"),
    MailVariable("submitted_at", "Data złożenia zgłoszenia.", "10.11.2026", "Daty i status"),
    MailVariable("submission_date", "Data złożenia zgłoszenia (alias).", "10.11.2026", "Daty i status"),
    MailVariable("status_label", "Czytelny status zgłoszenia.", "Złożono", "Daty i status"),
    MailVariable("current_status", "Bieżący status zgłoszenia.", "Złożono", "Daty i status"),
    MailVariable("process_status_label", "Czytelna nazwa statusu procesu.", "Złożono", "Daty i status"),
    MailVariable("current_stage_label", "Czytelna nazwa bieżącego etapu.", "Weryfikacja", "Daty i status"),
    MailVariable("imie", "Imię uczestnika, jeśli jest dostępne.", "Jan", "Uczestnik"),
    MailVariable("imiona", "Imiona uczestnika, jeśli są dostępne.", "Jan", "Uczestnik"),
    MailVariable("nazwisko", "Nazwisko uczestnika, jeśli jest dostępne.", "Kowalski", "Uczestnik"),
    MailVariable("selected_trainings_count", "Liczba wybranych szkoleń.", "2", "Szkolenia"),
    MailVariable("available_trainings", "Dostępne szkolenia jako domyślna tabela HTML.", "", "Szkolenia"),
    MailVariable("available_trainings_table", "Dostępne szkolenia jako tabela HTML.", "", "Szkolenia"),
    MailVariable("available_trainings_list", "Dostępne szkolenia jako lista HTML.", "", "Szkolenia"),
    MailVariable("available_trainings_text", "Dostępne szkolenia w formacie tekstowym.", "Szkolenie A", "Szkolenia"),
    MailVariable("available_trainings_list_text", "Dostępne szkolenia jako lista tekstowa.", "Szkolenie A", "Szkolenia"),
    MailVariable("selected_trainings_table", "Wybrane szkolenia jako tabela HTML.", "", "Szkolenia"),
    MailVariable("selected_trainings_list", "Wybrane szkolenia jako lista HTML.", "", "Szkolenia"),
    MailVariable("selected_trainings_text", "Lista wybranych szkoleń w formacie tekstowym.", "Szkolenie A", "Szkolenia"),
    MailVariable("trainings_total_price", "Łączna cena wybranych szkoleń.", "1 200,00 zł", "Szkolenia"),
    MailVariable("pdf_filename", "Nazwa pliku PDF zgłoszenia, jeśli jest dostępna.", "zgloszenie.pdf", "Dokumenty"),
    MailVariable("declaration_filename", "Nazwa pliku deklaracji, jeśli jest dostępna.", "deklaracja.pdf", "Dokumenty"),
    MailVariable("agreement_filename", "Nazwa pliku umowy, jeśli jest dostępna.", "umowa.pdf", "Dokumenty"),
    MailVariable("signed_pdf_filename", "Nazwa podpisanego PDF, jeśli jest dostępna.", "zgloszenie-podpisane.pdf", "Dokumenty"),
    MailVariable("declaration_signed_filename", "Nazwa podpisanej deklaracji, jeśli jest dostępna.", "deklaracja-podpisana.pdf", "Dokumenty"),
    MailVariable("agreement_signed_filename", "Nazwa podpisanej umowy, jeśli jest dostępna.", "umowa-podpisana.pdf", "Dokumenty"),
    MailVariable("public_status_url", "Adres strony statusu tylko do odczytu.", "", "Linki"),
    MailVariable("status_url", "Adres statusu zgłoszenia, gdy jest dostępny.", "", "Linki"),
    MailVariable("participant_action_url", "Indywidualny adres czynności uczestnika.", "", "Linki"),
    MailVariable("document_url", "Chroniony adres dostępnego dokumentu.", "", "Linki"),
    MailVariable("podpisz_url", "Chroniony adres czynności podpisania.", "", "Linki"),
    MailVariable("pobierz_url", "Chroniony adres pobrania dokumentu.", "", "Linki"),
)

_EVENT_VARIABLES = {
    "submission_received": (),
    "auto_rejected_by_condition": (
        MailVariable("qualification_message", "Uzasadnienie automatycznej kwalifikacji.", "Warunki nie zostały spełnione.", "Kwalifikacja"),
        MailVariable("qualification_message_html", "Uzasadnienie przygotowane do użycia w HTML.", "", "Kwalifikacja"),
    ),
    "returned_for_correction": (
        MailVariable("correction_reason", "Powód zwrotu do korekty.", "Brak podpisu.", "Korekta"),
        MailVariable("correction_message", "Wiadomość urzędnika do uczestnika.", "Uzupełnij dane.", "Korekta"),
        MailVariable("correction_clear_info", "Informacja o zachowaniu lub wyczyszczeniu danych.", "Dane zachowano.", "Korekta"),
        MailVariable("correction_url", "Indywidualny adres formularza korekty.", "", "Linki"),
        MailVariable("correction_reason_html", "Powód korekty przygotowany do użycia w HTML.", "", "Korekta"),
        MailVariable("correction_message_html", "Wiadomość przygotowana do użycia w HTML.", "", "Korekta"),
        MailVariable("correction_clear_info_html", "Informacja o danych przygotowana do użycia w HTML.", "", "Korekta"),
    ),
    "form_draft_resume": (
        MailVariable("form_name", "Nazwa formularza.", "Wniosek o szkolenie", "Formularz"),
        MailVariable("form_title", "Tytuł formularza.", "Wniosek o szkolenie", "Formularz"),
        MailVariable("form_slug", "Identyfikator formularza w adresie.", "wniosek-szkolenie", "Formularz"),
        MailVariable("draft_expires_at", "Data wygaśnięcia linku wersji roboczej.", "2026-11-17T12:00:00", "Daty i status"),
        MailVariable("draft_resume_url", "Indywidualny adres wznowienia wersji roboczej.", "", "Linki"),
    ),
    "new_submission_admin": (
        MailVariable("submission_id", "Publiczny numer zgłoszenia.", "ZGŁ-2026-001"),
        MailVariable("form_name", "Nazwa formularza.", "Wniosek o szkolenie", "Formularz"),
    ),
    "document_received": (
        MailVariable("submission_id", "Publiczny numer zgłoszenia.", "ZGŁ-2026-001"),
        MailVariable("form_name", "Nazwa formularza.", "Wniosek o szkolenie", "Formularz"),
        MailVariable("mail_documents", "Lista nazw przyjętych dokumentów.", "Umowa.pdf", "Dokumenty"),
        MailVariable("mail_action_url", "Adres zgłoszenia w panelu administracyjnym.", "", "Linki"),
    ),
    "stage_rollback": (
        MailVariable("rollback_reason", "Powód cofnięcia etapu.", "Wymagana poprawka.", "Cofnięcie etapu"),
        MailVariable("rollback_reason_text", "Powód cofnięcia etapu w wersji tekstowej.", "Wymagana poprawka.", "Cofnięcie etapu"),
        MailVariable("rollback_status_label", "Nazwa etapu po cofnięciu.", "Weryfikacja", "Cofnięcie etapu"),
        MailVariable("rollback_next_action", "Następna czynność uczestnika.", "Sprawdź zgłoszenie.", "Cofnięcie etapu"),
        MailVariable("rollback_next_action_text", "Następna czynność w wersji tekstowej.", "Sprawdź zgłoszenie.", "Cofnięcie etapu"),
    ),
    "agreement_signed_by_office": (
        MailVariable("agreement_number", "Numer umowy, jeśli jest dostępny.", "UM/2026/1", "Umowa"),
        MailVariable("signed_agreements_text", "Lista podpisanych umów w formacie tekstowym.", "Umowa.pdf", "Umowa"),
        MailVariable("signed_agreements_table", "Lista podpisanych umów w HTML.", "", "Umowa"),
        MailVariable("signed_agreement_download_link", "Chroniony adres pobrania podpisanej umowy.", "", "Linki"),
    ),
    "agreement_rejected_by_office": (
        MailVariable("agreement_decision_reason", "Uzasadnienie odrzucenia umowy.", "Brak wymaganych danych.", "Umowa"),
        MailVariable("agreement_decision_reason_text", "Uzasadnienie odrzucenia w wersji tekstowej.", "Brak wymaganych danych.", "Umowa"),
    ),
    "training_attendance_invitation": (
        MailVariable("session_name", "Nazwa terminu szkolenia.", "Szkolenie Excel", "Szkolenie"),
        MailVariable("mail_action_url", "Indywidualny adres potwierdzenia obecności.", "", "Linki"),
    ),
    "training_survey_invitation": (
        MailVariable("survey_name", "Nazwa ankiety szkoleniowej.", "Ankieta po szkoleniu", "Szkolenie"),
        MailVariable("survey_description", "Opis ankiety szkoleniowej.", "Oceń szkolenie.", "Szkolenie"),
        MailVariable("mail_action_url", "Indywidualny adres ankiety.", "", "Linki"),
    ),
}


def system_mail_variables(event_type: str) -> tuple[MailVariable, ...]:
    """Visible variables for a registered event, without internal context keys."""
    mail_type = SYSTEM_MAIL_BY_ID.get(event_type)
    if mail_type is None:
        raise KeyError(event_type)
    if mail_type.source_policy in {"dynamic"}:
        common = () if event_type in {"new_submission_admin", "document_received"} else _COMMON_VARIABLES
    elif event_type == "form_draft_resume":
        common = ()
    else:
        common = _COMMON_VARIABLES
    return common + tuple(replace(variable, scope="Tylko to zdarzenie")
                          for variable in _EVENT_VARIABLES[event_type])


def load_default_template(event_type: str):
    """Read one registered UTF-8 template set from the fixed project directory."""
    mail_type = SYSTEM_MAIL_BY_ID.get(event_type)
    if mail_type is None:
        raise SystemMailTemplateError("Unknown system mail type")
    root = DEFAULT_TEMPLATE_ROOT.resolve()
    directory = (root / mail_type.id).resolve()
    if directory.parent != root:
        raise SystemMailTemplateError(f"Unsafe system mail template path: {event_type}")
    contents = {}
    for filename in DEFAULT_TEMPLATE_FILES:
        path = (directory / filename).resolve()
        if path.parent != directory:
            raise SystemMailTemplateError(f"Unsafe system mail template path: {event_type}/{filename}")
        try:
            value = path.read_text(encoding="utf-8").rstrip("\r\n")
        except (OSError, UnicodeError) as exc:
            raise SystemMailTemplateError(f"Cannot read system mail template: {event_type}/{filename}") from exc
        if not value.strip():
            raise SystemMailTemplateError(f"Empty system mail template: {event_type}/{filename}")
        contents[filename] = value
    html_body = contents["body.html"]
    metadata = {}
    if html_body.startswith("<!-- system-mail: "):
        header, separator, html_body = html_body.partition("\n")
        if not separator or not header.endswith(" -->"):
            raise SystemMailTemplateError(f"Invalid system mail metadata: {event_type}")
        try:
            metadata = json.loads(header[len("<!-- system-mail: "):-len(" -->")])
        except (ValueError, TypeError) as exc:
            raise SystemMailTemplateError(f"Invalid system mail metadata: {event_type}") from exc
        if not isinstance(metadata, dict) or not html_body.strip():
            raise SystemMailTemplateError(f"Invalid system mail metadata: {event_type}")
        if not isinstance(metadata.get("context_defaults", {}), dict):
            raise SystemMailTemplateError(f"Invalid system mail metadata: {event_type}")
        for key in ("content_title", "action_text", "action_link_label"):
            if not isinstance(metadata.get(key, ""), str):
                raise SystemMailTemplateError(f"Invalid system mail metadata: {event_type}")
    return SimpleNamespace(
        id=0,
        name=mail_type.name,
        subject=contents["subject.txt"],
        html_body=html_body,
        text_body=contents["body.txt"],
        content_title=metadata.get("content_title", ""),
        mail_action_text_template=metadata.get("action_text", ""),
        mail_action_link_label_template=metadata.get("action_link_label", ""),
        context_defaults=metadata.get("context_defaults", {}),
        is_active=True,
        show_process_status=mail_type.show_process_status,
        use_platform_layout=True,
    )
