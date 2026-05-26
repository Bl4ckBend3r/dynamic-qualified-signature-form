# Instrukcja tworzenia JSON formularza oraz szablonów HTML

Ten dokument opisuje aktualny sposób przygotowania formularza i szablonów dokumentów dla aplikacji.

Źródłem prawdy dla formularzy i szablonów jest Nextcloud. Lokalny katalog `forms/` służy tylko do developmentu i testów.

---

## 1. Lokalizacja plików w Nextcloud

Przykładowa struktura:

```text
Strona WWW/
└── Formularze/
    ├── sample_form.json
    └── Template/
        ├── deklaracja-wiedza-kluczem.html
        ├── umowa-wiedza-kluczem.html
        └── Mail/
            ├── potwierdzenie.html
            └── agreement_signed.html
```

Jeżeli w `.env` ustawiono:

```env
NEXTCLOUD_FORMS_DIR=Strona WWW/Formularze
```

to ścieżka w JSON:

```json
"template": "Template/deklaracja-wiedza-kluczem.html"
```

oznacza:

```text
Strona WWW/Formularze/Template/deklaracja-wiedza-kluczem.html
```

Nie dodawaj spacji na początku ścieżki.

Poprawnie:

```json
"template": "Template/deklaracja-wiedza-kluczem.html"
```

Niepoprawnie:

```json
"template": " Template/deklaracja-wiedza-kluczem.html"
```

---

## 2. Minimalny JSON formularza

```json
{
  "title": "Nazwa formularza",
  "description": "Opis formularza widoczny pod tytułem.",
  "header_image": "Logo/logo.png",
  "submit_label": "Generuj i wyślij",
  "signature": {
    "mode": "none",
    "allow_trusted_profile": false,
    "allow_qualified_signature": false,
    "require_before_submit": false,
    "show_user_choice": false
  },
  "documents": [],
  "workflow": {
    "initial_step": "submission",
    "steps": []
  },
  "rules": [],
  "notifications": [],
  "fields": []
}
```

Sekcje `workflow`, `rules` i `notifications` są opcjonalne. Jeśli `workflow` nie istnieje, aplikacja zbuduje domyślny workflow na podstawie dokumentów.

---

## 3. Sekcja `signature`

Podpis nie jest wykonywany w głównym formularzu zgłoszeniowym. Użytkownik najpierw wysyła formularz, a później pobiera PDF, podpisuje go zewnętrznie i wgrywa podpisany plik.

Zalecana konfiguracja:

```json
"signature": {
  "mode": "none",
  "allow_trusted_profile": false,
  "allow_qualified_signature": false,
  "require_before_submit": false,
  "show_user_choice": false
}
```

---

## 4. Sekcja `documents`

Nowy zalecany format to lista dokumentów:

```json
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
]
```

### Stary format nadal działa

Stare formularze z `process.documents` są wspierane:

```json
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
```

`FormConfigService` normalizuje oba formaty do `form_config["documents"]`.

---

## 5. Deklaracja

Jeżeli użytkownik ma uzupełnić dane deklaracji na stronie, pola wpisz w dokumencie `declaration`.

```json
{
  "id": "declaration",
  "enabled": true,
  "template": "Template/deklaracja-wiedza-kluczem.html",
  "filename_pattern": "{first_name}_{last_name}-deklaracja.pdf",
  "form_title": "Uzupełnienie deklaracji uczestnictwa",
  "form_description": "Uzupełnij pola wymagane do wygenerowania deklaracji uczestnictwa.",
  "form_submit_label": "Wygeneruj deklarację PDF",
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

Jeżeli deklaracja nie jest wymagana:

```json
{
  "id": "declaration",
  "enabled": false
}
```

---

## 6. Umowa

Umowa nie ma dodatkowego formularza dla uczestnika. Jest generowana z danych zapisanych w zgłoszeniu i z danych deklaracji.

Pojedyncza umowa:

```json
{
  "id": "agreement",
  "enabled": true,
  "kind": "generated_pdf",
  "template": "Template/umowa-wiedza-kluczem.html",
  "filename_pattern": "{first_name}_{last_name}-umowa.pdf",
  "signature_required": true
}
```

Umowy szkoleniowe generowane po wybranych szkoleniach:

```json
{
  "id": "training_agreement",
  "enabled": true,
  "kind": "generated_pdf",
  "template": "Template/umowa-wiedza-kluczem.html",
  "filename_pattern": "{first_name}_{last_name}-{training_id}-umowa.pdf",
  "signature_required": true,
  "repeat_over": "selected_trainings",
  "repeat_item_alias": "training",
  "numbering": {
    "number_pattern": "{submission_id}/{agreement_sequence}/{generated_date}"
  }
}
```

Data wygenerowania umowy jest automatyczna. Nie dodawaj pola daty do strony.

W szablonie umowy używaj:

```html
{{ agreement_number }}
{{ generated_date }}
{{ agreement_generated_at }}
```

---

## 7. Typy pól formularza

Najczęściej używane:

```text
section
static_text
text
email
number
date
tel
pesel
textarea
select
radio
checkbox
training_selection
```

Przykład pola tekstowego:

```json
{
  "type": "text",
  "name": "imiona",
  "label": "Imię (imiona)",
  "placeholder": "Wpisz imię lub imiona",
  "required": true,
  "width": "half"
}
```

Przykład zgody:

```json
{
  "type": "checkbox",
  "name": "accept_rodo",
  "required": true,
  "width": "full",
  "options": [
    {
      "value": "Tak",
      "label": "Wyrażam zgodę na przetwarzanie danych osobowych."
    }
  ]
}
```

Przykład wyboru szkoleń:

```json
{
  "type": "training_selection",
  "name": "selected_trainings",
  "label": "Wybierz szkolenia",
  "required": true,
  "max_total_amount": 5000,
  "currency": "PLN",
  "catalog": [
    { "id": "excel", "name": "Excel", "price": 1200 },
    { "id": "angielski", "name": "Angielski", "price": 1000 }
  ]
}
```

---

## 8. Widoczność warunkowa

Przykład:

```json
{
  "type": "textarea",
  "name": "specjalne_potrzeby_opis",
  "label": "Opis specjalnych potrzeb",
  "required": true,
  "width": "full",
  "visible_if": {
    "field": "specjalne_potrzeby",
    "operator": "equals",
    "value": "Tak"
  }
}
```

W formularzu obsługiwane są proste operatory widoczności:

```text
equals
not_equals
```

---

## 9. Reguły procesu

Reguły blokowania umowy nie powinny być zaszyte w kodzie. Dodaj je do JSON:

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
      {
        "action": "set_field",
        "field": "agreement_blocked",
        "value": "Tak"
      },
      {
        "action": "set_field",
        "field": "agreement_block_reason",
        "value": "Warunki nie zostały spełnione na podstawie deklaracji uczestnika."
      },
      {
        "action": "set_status",
        "value": "AGREEMENT_BLOCKED"
      }
    ]
  }
]
```

Obsługiwane warunki:

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

---

## 10. Powiadomienia e-mail

Szablony maili mogą być w Nextcloud, na przykład:

```text
Strona WWW/Formularze/Template/Mail/agreement_signed.html
```

Konfiguracja:

```json
"notifications": [
  {
    "event": "FORM_SUBMITTED",
    "to": ["participant"],
    "template": "Template/Mail/potwierdzenie.html",
    "subject": "Potwierdzenie zgłoszenia"
  },
  {
    "event": "AGREEMENT_SIGNED",
    "to": ["form_notifications"],
    "template": "Template/Mail/agreement_signed.html",
    "subject": "Umowa podpisana przez uczestnika"
  }
]
```

Odbiorcy:

```text
participant
form_notifications
field:nazwa_pola
koordynator@example.com
```

`form_notifications` używa:

```env
FORM_NOTIFICATION_EMAILS=koordynator@example.com
```

Jeżeli `AGREEMENT_SIGNED` nie jest skonfigurowane w JSON, aplikacja użyje fallbacku:

```text
Template/Mail/agreement_signed.html
```

---

## 11. Zmienne w szablonach dokumentów

Najważniejsze:

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

Dla umowy szkoleniowej:

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

Przykład listy wybranych szkoleń w deklaracji:

```html
{% if selected_trainings %}
  <h3>Wybrane szkolenia</h3>
  <ol>
    {% for training in selected_trainings %}
      <li>{{ training.get("name") }}{% if training.get("price") %} - {{ training.get("price") }} PLN{% endif %}</li>
    {% endfor %}
  </ol>
{% endif %}
```

---

## 12. Minimalny szablon deklaracji

```html
<!doctype html>
<html lang="pl">
<head>
  <meta charset="UTF-8">
  <title>Deklaracja uczestnictwa</title>
</head>
<body class="document document--declaration">
  <h1>Deklaracja uczestnictwa</h1>

  <p>
    Ja niżej podpisany/a <strong>{{ participant_name }}</strong>,
    PESEL <strong>{{ submission.get("pesel", "") }}</strong>,
    deklaruję udział w projekcie.
  </p>

  <table>
    <tr>
      <th>Kryterium</th>
      <th>TAK</th>
      <th>NIE</th>
    </tr>
    <tr>
      <td>Ukończyłem/-am 18 r. życia</td>
      <td>{% if submission.get("deklaracja_18_lat") == "Tak" %}●{% else %}○{% endif %}</td>
      <td>{% if submission.get("deklaracja_18_lat") == "Nie" %}●{% else %}○{% endif %}</td>
    </tr>
  </table>

  <p>ID zgłoszenia: {{ submission_id }}</p>
</body>
</html>
```

---

## 13. Minimalny szablon umowy

```html
<!doctype html>
<html lang="pl">
<head>
  <meta charset="UTF-8">
  <title>Umowa uczestnictwa</title>
</head>
<body class="document document--agreement">
  <h1>Umowa uczestnictwa nr {{ agreement_number|default(submission_id, true) }}</h1>

  <p>Uczestnik: <strong>{{ participant_name }}</strong></p>
  <p>PESEL: <strong>{{ submission.get("pesel", "") }}</strong></p>

  {% if training_name %}
    <p>Szkolenie: <strong>{{ training_name }}</strong></p>
  {% endif %}

  <p>Data wygenerowania umowy: {{ generated_date }}</p>

  <table class="signature-table">
    <tr>
      <td>Beneficjent / urząd</td>
      <td>Uczestnik projektu</td>
    </tr>
  </table>
</body>
</html>
```

---

## 14. Podpisy dokumentów

Użytkownik podpisuje dokumenty zewnętrznie i wgrywa podpisane PDF-y.

Dopuszczalne podpisy:

```text
mSzafir
Profil Zaufany
```

Aktualna kolejność:

1. Użytkownik podpisuje deklarację.
2. System weryfikuje deklarację.
3. System generuje umowę.
4. Użytkownik podpisuje umowę.
5. System weryfikuje umowę i wysyła `AGREEMENT_SIGNED`.
6. Urząd podpisuje umowę później w procesie ręcznym albo przyszłej integracji.

---

## 15. Najczęstsze błędy

### Brak pól deklaracji

Sprawdź:

```text
documents[].id = declaration
documents[].fields
```

albo stary format:

```text
process.documents.declaration.fields
```

### Nie znaleziono szablonu dokumentu

Sprawdź:

- czy plik istnieje w Nextcloud,
- czy ścieżka nie ma spacji na początku,
- czy wielkość liter w nazwie katalogu jest poprawna,
- czy plik ma rozszerzenie `.html`.

### Umowa się nie odblokowuje

Sprawdź:

```text
declaration_signature_valid = Tak
agreement_blocked
agreement_block_reason
rules
```

Jeżeli reguły zarządzają blokadą i warunki blokady nie są spełnione, aplikacja czyści stare pole `agreement_blocked`.

### Mail nie wychodzi

Sprawdź:

```text
SMTP_HOST
SMTP_USER
SMTP_PASSWORD
MAIL_FROM
FORM_NOTIFICATION_EMAILS
notifications
Template/Mail/agreement_signed.html
```

### PDF zwraca 403

Link do PDF musi zawierać token:

```text
?token=<access_token>
```

Linki buduje aplikacja przez `DocumentService.build_download_url()`.

---

## 16. Walidacja JSON

Jeżeli szablony są tylko w Nextcloud:

```powershell
python manage.py validate-form forms/sample_form.json --skip-template-check
```

Jeżeli szablony są lokalnie:

```powershell
python manage.py validate-form forms/sample_form.json --template-root C:\path\to\templates
```
