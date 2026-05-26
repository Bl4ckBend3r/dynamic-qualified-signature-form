# Konfiguracja dokumentów w JSON formularza

Dokumenty projektowe są konfigurowane w definicji formularza JSON przechowywanej w Nextcloud. Lokalny katalog `forms/` jest tylko pomocniczy dla developmentu i testów. W środowisku produkcyjnym źródłem prawdy jest storage/Nextcloud.

## Aktualny model konfiguracji

Kod normalizuje konfigurację formularza przez:

```text
services/form_config_service.py
```

Po normalizacji kod korzysta z:

```text
form_config["documents"]
form_config["workflow"]
form_config["rules"]
form_config["notifications"]
```

Stary format:

```text
process.documents
```

jest nadal obsługiwany i łączony z nowym `documents`.

---

## Zalecany format `documents`

Nowe formularze powinny używać listy dokumentów:

```json
{
  "title": "Projekt Wiedza kluczem do sukcesu",
  "documents": [
    {
      "id": "declaration",
      "label": "Deklaracja uczestnictwa",
      "kind": "generated_pdf",
      "template": "Template/deklaracja-wiedza-kluczem.html",
      "filename_pattern": "{first_name}_{last_name}-deklaracja.pdf",
      "signature_required": true,
      "allowed_signatures": ["mszafir", "profil_zaufany"],
      "form_title": "Uzupełnienie deklaracji uczestnictwa",
      "form_description": "Uzupełnij pola wymagane do wygenerowania deklaracji uczestnictwa.",
      "form_submit_label": "Wygeneruj deklarację PDF",
      "fields": []
    },
    {
      "id": "training_agreement",
      "label": "Umowa szkoleniowa",
      "kind": "generated_pdf",
      "template": "Template/umowa-wiedza-kluczem.html",
      "filename_pattern": "{first_name}_{last_name}-{training_id}-umowa.pdf",
      "signature_required": true,
      "allowed_signatures": ["mszafir", "profil_zaufany"],
      "repeat_over": "selected_trainings",
      "repeat_item_alias": "training",
      "numbering": {
        "number_pattern": "{submission_id}/{agreement_sequence}/{generated_date}"
      }
    }
  ],
  "fields": []
}
```

---

## Kompatybilny format `process.documents`

Starsze formularze nadal działają:

```json
{
  "process": {
    "documents": {
      "declaration": {
        "enabled": true,
        "template": "Template/deklaracja-wiedza-kluczem.html",
        "filename_pattern": "{first_name}_{last_name}-deklaracja.pdf",
        "signature_required": true,
        "fields": []
      },
      "agreement": {
        "enabled": true,
        "template": "Template/umowa-wiedza-kluczem.html",
        "filename_pattern": "{first_name}_{last_name}-umowa.pdf",
        "signature_required": true
      }
    }
  }
}
```

Jeżeli ten sam dokument występuje w `process.documents` i w `documents`, konfiguracje są łączone po `id`. Dzięki temu stare pola deklaracji nie giną po dodaniu nowego metadokumentu.

---

## Dokumenty obsługiwane obecnie

### `declaration`

Deklaracja może mieć własny formularz uzupełniający:

```json
{
  "id": "declaration",
  "fields": [
    {
      "type": "radio",
      "name": "deklaracja_18_lat",
      "label": "Ukończyłem/-am 18 r. życia",
      "required": true,
      "options": ["Tak", "Nie"],
      "width": "full"
    }
  ]
}
```

Po wysłaniu formularza deklaracji system zapisuje dane, stosuje reguły i generuje deklarację PDF.

### `agreement`

Pojedyncza umowa generowana z danych zgłoszenia:

```json
{
  "id": "agreement",
  "kind": "generated_pdf",
  "template": "Template/umowa-wiedza-kluczem.html",
  "filename_pattern": "{first_name}_{last_name}-umowa.pdf",
  "signature_required": true
}
```

### `training_agreement`

Umowy generowane po jednej dla każdego elementu kolekcji, na przykład po wybranych szkoleniach:

```json
{
  "id": "training_agreement",
  "kind": "generated_pdf",
  "template": "Template/umowa-wiedza-kluczem.html",
  "filename_pattern": "{first_name}_{last_name}-{training_id}-umowa.pdf",
  "repeat_over": "selected_trainings",
  "repeat_item_alias": "training",
  "numbering": {
    "number_pattern": "{submission_id}/{agreement_sequence}/{generated_date}"
  }
}
```

Jeżeli formularz ma tylko stare `agreement`, trasa generowania umów potrafi tymczasowo zbudować adapter `training_agreement` dla wybranych szkoleń.

---

## Ścieżki szablonów

Jeżeli ścieżka nie zaczyna się od `NEXTCLOUD_FORMS_DIR` ani `NEXTCLOUD_OUTPUT_DIR`, system traktuje ją jako relatywną do katalogu formularzy w Nextcloud.

Przykład:

```json
"template": "Template/deklaracja-wiedza-kluczem.html"
```

Przy konfiguracji:

```env
NEXTCLOUD_FORMS_DIR=Strona WWW/Formularze
```

oznacza:

```text
Strona WWW/Formularze/Template/deklaracja-wiedza-kluczem.html
```

To samo dotyczy szablonów maili:

```json
"template": "Template/Mail/agreement_signed.html"
```

---

## Zmienne dostępne w szablonie dokumentu

Szablony HTML dokumentów są renderowane przez Jinja.

Najważniejsze zmienne:

```text
form_definition
submission_id
participant_name
submission
submission_view
consents_view
pdf_image_url
pdf_image_alt
document_type
selected_trainings
training_agreements
selected_trainings_total
```

Dla umów powtarzanych dostępne są dodatkowo:

```text
training
training_id
training_name
training_price
agreement_sequence
agreement_number
generated_date
agreement_generated_at
```

Przykład:

```html
<h1>Umowa nr {{ agreement_number|default(submission_id, true) }}</h1>
<p>Uczestnik: {{ participant_name }}</p>
<p>Szkolenie: {{ training_name }}</p>
```

---

## Nazwy plików

`filename_pattern` obsługuje:

```text
{first_name}
{last_name}
{participant_name}
{submission_id}
{training_id}
{agreement_sequence}
{generated_date}
```

Przykład:

```json
"filename_pattern": "{first_name}_{last_name}-{training_id}-umowa.pdf"
```

---

## Data i numer umowy

Data wygenerowania umowy jest ustawiana automatycznie w momencie generowania:

```text
generated_date
agreement_generated_at
```

Nie ma pola wyboru daty na stronie.

Numer umowy można skonfigurować w dokumencie:

```json
"numbering": {
  "number_pattern": "{submission_id}/{agreement_sequence}/{generated_date}"
}
```

W HTML używaj:

```html
{{ agreement_number }}
```

---

## Reguły

Reguły są w sekcji `rules`.

```json
"rules": [
  {
    "id": "block_agreement_if_not_eligible",
    "when": {
      "any": [
        { "field": "deklaracja_18_lat", "equals": "Nie" },
        { "field": "deklaracja_lubuskie", "equals": "Nie" }
      ]
    },
    "then": [
      { "action": "set_field", "field": "agreement_blocked", "value": "Tak" },
      { "action": "set_field", "field": "agreement_block_reason", "value": "Warunki nie zostały spełnione na podstawie deklaracji uczestnika." },
      { "action": "set_status", "value": "AGREEMENT_BLOCKED" }
    ]
  }
]
```

Obsługiwane operatory:

```text
equals
not_equals
in
not_in
any
all
```

Obsługiwane akcje:

```text
set_field
set_status
block_document
unblock_document
```

Jeżeli formularz nie ma `rules`, aplikacja zachowuje fallback dla obecnego procesu.

---

## Powiadomienia

Powiadomienia są w sekcji `notifications`.

Przykład po poprawnym uploadzie umowy podpisanej przez uczestnika:

```json
"notifications": [
  {
    "event": "AGREEMENT_SIGNED",
    "to": ["form_notifications"],
    "template": "Template/Mail/agreement_signed.html",
    "subject": "Umowa podpisana przez uczestnika"
  }
]
```

Obsługiwani odbiorcy:

```text
participant
form_notifications
field:nazwa_pola
adres@example.com
```

`form_notifications` czyta adresy z:

```env
FORM_NOTIFICATION_EMAILS=koordynator@example.com
```

Jeżeli nie skonfigurujesz `AGREEMENT_SIGNED`, aplikacja użyje fallbacku:

```text
Template/Mail/agreement_signed.html
```

---

## Workflow

Sekcja `workflow` może być jawna:

```json
"workflow": {
  "initial_step": "submission",
  "steps": [
    { "id": "submission", "type": "form_submit", "next": "officer_review" },
    {
      "id": "officer_review",
      "type": "manual_decision",
      "decisions": {
        "accepted": "declaration",
        "rejected": "end_rejected"
      }
    },
    {
      "id": "declaration",
      "type": "generate_document",
      "document_id": "declaration",
      "next": "declaration_signature"
    },
    {
      "id": "declaration_signature",
      "type": "signature_upload",
      "document_id": "declaration",
      "next": "training_agreements"
    },
    {
      "id": "training_agreements",
      "type": "generate_documents",
      "document_id": "training_agreement",
      "repeat_over": "selected_trainings",
      "next": "training_agreements_signature"
    },
    {
      "id": "training_agreements_signature",
      "type": "signature_upload_many",
      "document_id": "training_agreement",
      "repeat_over": "selected_trainings",
      "next": "completed"
    },
    { "id": "end_rejected", "type": "end" },
    { "id": "completed", "type": "end" }
  ]
}
```

Jeżeli `workflow` nie istnieje, `FormConfigService` buduje domyślny workflow na podstawie dokumentów.

---

## Linki do PDF

Linki do PDF muszą zawierać token:

```text
/downloads/pdfs/<slug>/<filename>?token=<access_token>
/downloads/signed/<slug>/<filename>?token=<access_token>
```

Nie twórz linków ręcznie w szablonach. Kod powinien używać:

```python
DocumentService.build_download_url(...)
```

---

## Walidacja konfiguracji

Walidacja lokalnego JSON:

```powershell
python manage.py validate-form forms/sample_form.json --skip-template-check
```

Jeżeli szablony są lokalnie:

```powershell
python manage.py validate-form forms/sample_form.json --template-root C:\path\to\templates
```

Używaj `--skip-template-check`, gdy szablony są tylko w Nextcloud.

---

## Aktualny zakres implementacji

Zaimplementowane:

- `app.py` jako Flask application factory,
- blueprinty dla formularzy, dokumentów i API,
- kontener serwisów,
- repozytorium zgłoszeń CSV/Nextcloud,
- repozytorium audit logu,
- normalizacja `documents`, `workflow`, `rules`, `notifications`,
- kompatybilność ze starym `process.documents`,
- generowanie deklaracji,
- generowanie pojedynczej umowy,
- generowanie umów powtarzanych po `repeat_over`,
- automatyczna data generowania umowy,
- upload i weryfikacja podpisanej deklaracji,
- upload i weryfikacja umowy podpisanej przez uczestnika,
- powiadomienie `AGREEMENT_SIGNED`,
- tokeny dostępu do PDF,
- walidator konfiguracji formularza.

Nie jest jeszcze zaimplementowany osobny publiczny workflow uploadu umowy podpisanej przez urząd.
