# Instrukcja tworzenia plików JSON i HTML

Dokument opisuje sposób przygotowania:

- pliku JSON formularza,
- szablonu HTML deklaracji,
- szablonu HTML umowy,
- konfiguracji procesu dokumentów w Nextcloud.

Instrukcja jest przeznaczona dla osób przygotowujących nowe formularze i dokumenty projektowe bez zmiany kodu aplikacji.

---

## 1. Lokalizacja plików w Nextcloud

Przykładowa struktura katalogów:

```text
Strona WWW/
└── Formularze/
    ├── sample_form.json
    └── Template/
        ├── deklaracja-wiedza-kluczem.html
        └── umowa-wiedza-kluczem.html
```

Jeżeli w `.env` ustawiono:

```env
NEXTCLOUD_FORMS_DIR=Strona WWW/Formularze
```

wtedy ścieżka w JSON:

```json
"template": "Template/deklaracja-wiedza-kluczem.html"
```

oznacza plik:

```text
Strona WWW/Formularze/Template/deklaracja-wiedza-kluczem.html
```

### Ważne

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

## 2. Podstawowa struktura pliku JSON formularza

Minimalny plik formularza:

```json
{
  "title": "Nazwa formularza",
  "description": "Opis formularza widoczny pod tytułem.",
  "header_image": "forms/logo.png",
  "submit_label": "Generuj i wyślij",
  "signature": {
    "mode": "none",
    "allow_trusted_profile": false,
    "allow_qualified_signature": false,
    "require_before_submit": false,
    "show_user_choice": false
  },
  "process": {
    "documents": {
      "declaration": {
        "enabled": true,
        "template": "Template/deklaracja.html",
        "filename_pattern": "{first_name}_{last_name}-deklaracja.pdf",
        "signature_required": true,
        "fields": []
      },
      "agreement": {
        "enabled": true,
        "template": "Template/umowa.html",
        "filename_pattern": "{first_name}_{last_name}-umowa.pdf",
        "signature_required": true
      }
    }
  },
  "fields": []
}
```

---

## 3. Sekcja `signature`

Dla obecnego procesu podpis jest wykonywany później, po wygenerowaniu dokumentu PDF.

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

## 4. Sekcja `process.documents`

Sekcja `process.documents` określa, czy formularz ma deklarację i umowę.

### Deklaracja włączona

```json
"declaration": {
  "enabled": true,
  "template": "Template/deklaracja-wiedza-kluczem.html",
  "filename_pattern": "{first_name}_{last_name}-deklaracja.pdf",
  "signature_required": true,
  "form_title": "Uzupełnienie deklaracji uczestnictwa",
  "form_description": "Uzupełnij pola wymagane do wygenerowania deklaracji uczestnictwa.",
  "form_submit_label": "Wygeneruj deklarację PDF",
  "fields": []
}
```

### Deklaracja wyłączona

```json
"declaration": {
  "enabled": false
}
```

### Umowa włączona

Umowa jest generowana wyłącznie automatycznie na podstawie przesłanych danych. Nie dodawaj do niej `fields`.

```json
"agreement": {
  "enabled": true,
  "template": "Template/umowa-wiedza-kluczem.html",
  "filename_pattern": "{first_name}_{last_name}-umowa.pdf",
  "signature_required": true
}
```

### Umowa wyłączona

```json
"agreement": {
  "enabled": false
}
```

---

## 5. Typy pól w `fields`

Każde pole formularza jest obiektem w tablicy `fields`.

### Sekcja

```json
{
  "type": "section",
  "label": "Dane kandydata / kandydatki"
}
```

### Tekst statyczny

```json
{
  "type": "static_text",
  "label": "Treść informacyjna wyświetlana użytkownikowi."
}
```

### Pole tekstowe

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

Obsługiwane proste typy pól:

```text
text
email
number
date
tel
pesel
```

### Textarea

```json
{
  "type": "textarea",
  "name": "uwagi",
  "label": "Uwagi",
  "placeholder": "Wpisz dodatkowe informacje",
  "required": false,
  "width": "full"
}
```

### Select

```json
{
  "type": "select",
  "name": "wyksztalcenie",
  "label": "Wykształcenie",
  "required": true,
  "options": ["Podstawowe", "Średnie", "Wyższe"],
  "width": "half"
}
```

### Radio

```json
{
  "type": "radio",
  "name": "zamieszkuje_lubuskie",
  "label": "Zamieszkuję na terenie województwa lubuskiego",
  "required": true,
  "options": ["Tak", "Nie"],
  "width": "half"
}
```

### Checkbox dla zgód i oświadczeń

```json
{
  "type": "checkbox",
  "name": "osw_rodo",
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

---

## 6. Pola deklaracji uzupełniane przez uczestnika

Jeżeli uczestnik ma wypełnić część deklaracji na stronie przed wygenerowaniem PDF, pola należy dodać w:

```text
process.documents.declaration.fields
```

Przykład:

```json
"declaration": {
  "enabled": true,
  "template": "Template/deklaracja-wiedza-kluczem.html",
  "filename_pattern": "{first_name}_{last_name}-deklaracja.pdf",
  "signature_required": true,
  "form_title": "Uzupełnienie deklaracji uczestnictwa",
  "form_description": "Uzupełnij pola wymagane do wygenerowania deklaracji uczestnictwa.",
  "form_submit_label": "Wygeneruj deklarację PDF",
  "fields": [
    {
      "type": "section",
      "label": "Kryteria formalne udziału w projekcie"
    },
    {
      "type": "radio",
      "name": "deklaracja_18_lat",
      "label": "Ukończyłem/-am 18 r. życia",
      "required": true,
      "options": ["Tak", "Nie"],
      "width": "full"
    },
    {
      "type": "radio",
      "name": "deklaracja_lubuskie",
      "label": "Pracuję lub zamieszkuję lub przebywam na terenie województwa lubuskiego",
      "required": true,
      "options": ["Tak", "Nie"],
      "width": "full"
    }
  ]
}
```

### Reguła blokowania umowy

Dla projektu „Wiedza kluczem do sukcesu” umowa nie zostanie wygenerowana, jeżeli odpowiedź `Nie` pojawi się w jednym z pól:

```text
deklaracja_18_lat
deklaracja_lubuskie
deklaracja_brak_dzialalnosci
deklaracja_brak_ksztalcenia
deklaracja_umiejetnosci_podstawowe
```

System zapisze wtedy:

```text
agreement_blocked = Tak
agreement_block_reason = Warunki nie zostały spełnione...
process_status = AGREEMENT_BLOCKED
```

---

## 7. Wyświetlanie warunkowe pól

Przykład pola widocznego tylko po wybraniu `Tak`:

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

Obsługiwane operatory:

```text
equals
not_equals
```

---

## 8. Zmienne dostępne w szablonach HTML

Szablony HTML dokumentów używają składni Jinja.

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
```

Najczęściej używane:

```html
{{ participant_name }}
{{ submission_id }}
{{ submission.get("pesel", "") }}
{{ submission.get("email", "") }}
{{ submission.get("telefon", "") }}
```

Adres uczestnika:

```html
{{ submission.get("ulica", "") }}
{{ submission.get("nr_budynku", "") }}
{% if submission.get("nr_lokalu") %}/{{ submission.get("nr_lokalu") }}{% endif %},
{{ submission.get("kod_pocztowy", "") }}
{{ submission.get("miejscowosc", "") }},
woj. {{ submission.get("wojewodztwo", "") }}
```

---

## 9. Szablon HTML deklaracji

Plik przykładowy:

```text
Strona WWW/Formularze/Template/deklaracja-wiedza-kluczem.html
```

Minimalny przykład:

```html
<!doctype html>
<html lang="pl">
<head>
  <meta charset="UTF-8">
  <title>Deklaracja uczestnictwa</title>
  <style>
    @page { size: A4; margin: 18mm; }
    body { font-family: Arial, sans-serif; font-size: 11px; line-height: 1.45; }
    table { width: 100%; border-collapse: collapse; }
    th, td { border: 1px solid #999; padding: 6px; }
    .answer { width: 42px; text-align: center; }
  </style>
</head>
<body>
  <h1>Deklaracja uczestnictwa</h1>

  <p>
    Ja niżej podpisany/a <strong>{{ participant_name }}</strong>,
    PESEL <strong>{{ submission.get("pesel", "") }}</strong>,
    deklaruję udział w projekcie.
  </p>

  <table>
    <thead>
      <tr>
        <th>Lp.</th>
        <th>Kryterium</th>
        <th class="answer">TAK</th>
        <th class="answer">NIE</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td>1</td>
        <td>Ukończyłem/-am 18 r. życia</td>
        <td class="answer">{% if submission.get("deklaracja_18_lat") == "Tak" %}●{% else %}○{% endif %}</td>
        <td class="answer">{% if submission.get("deklaracja_18_lat") == "Nie" %}●{% else %}○{% endif %}</td>
      </tr>
    </tbody>
  </table>

  <p>
    Dokument wygenerowano automatycznie na podstawie formularza zgłoszeniowego oraz formularza deklaracji.
    ID zgłoszenia: {{ submission_id }}.
  </p>
</body>
</html>
```

---

## 10. Szablon HTML umowy

Umowa jest generowana tylko na podstawie danych zapisanych w systemie. Uczestnik nie wypełnia dodatkowego formularza umowy.

Plik przykładowy:

```text
Strona WWW/Formularze/Template/umowa-wiedza-kluczem.html
```

Minimalny przykład:

```html
<!doctype html>
<html lang="pl">
<head>
  <meta charset="UTF-8">
  <title>Umowa uczestnictwa</title>
  <style>
    @page { size: A4; margin: 18mm; }
    body { font-family: Arial, sans-serif; font-size: 11px; line-height: 1.45; }
    .signature-table { width: 100%; margin-top: 40px; border-collapse: collapse; }
    .signature-table td { width: 50%; text-align: center; padding-top: 35px; }
    .signature-line { display: block; border-top: 1px dotted #111; margin: 0 24px; padding-top: 6px; }
  </style>
</head>
<body>
  <h1>Umowa uczestnictwa nr {{ submission_id }}</h1>

  <p>
    Umowa uczestnictwa w projekcie „Wiedza kluczem do sukcesu” zawarta pomiędzy Beneficjentem a uczestnikiem:
  </p>

  <p>
    Imię i nazwisko: <strong>{{ participant_name }}</strong><br>
    PESEL: <strong>{{ submission.get("pesel", "") }}</strong><br>
    Adres zamieszkania:
    <strong>
      {{ submission.get("ulica", "") }}
      {{ submission.get("nr_budynku", "") }}
      {% if submission.get("nr_lokalu") %}/{{ submission.get("nr_lokalu") }}{% endif %},
      {{ submission.get("kod_pocztowy", "") }}
      {{ submission.get("miejscowosc", "") }}
    </strong>
  </p>

  <p>
    Uczestnik zobowiązuje się do aktywnego i systematycznego udziału we wszystkich przewidzianych dla niego formach wsparcia.
  </p>

  <table class="signature-table">
    <tr>
      <td><span class="signature-line">Beneficjent</span></td>
      <td><span class="signature-line">Uczestnik projektu</span></td>
    </tr>
  </table>
</body>
</html>
```

---

## 11. Nazwy plików dokumentów

W `filename_pattern` można używać:

```text
{first_name}
{last_name}
{participant_name}
{submission_id}
```

Przykład:

```json
"filename_pattern": "{first_name}_{last_name}-deklaracja.pdf"
```

---

## 12. Podpisy dokumentów

Po wygenerowaniu dokumentów użytkownik pobiera PDF, podpisuje go zewnętrznie i wgrywa podpisany plik.

Dopuszczalne podpisy:

```text
mSzafir
Profil Zaufany
```

System weryfikuje:

```text
czy PDF zawiera podpis,
czy podpis ma poprawną strukturę,
czy podpis jest dopuszczalnego typu,
czy dokument nie został zmieniony po podpisaniu w zakresie możliwym do rozpoznania przez weryfikator,
czy podpis może zostać sklasyfikowany jako mSzafir albo Profil Zaufany.
```

---

## 13. Najczęstsze błędy

### Brak pól deklaracji

Objaw:

```text
Ten formularz nie ma pól deklaracji do uzupełnienia.
```

Przyczyna: brak `process.documents.declaration.fields`.

Poprawna struktura:

```json
"process": {
  "documents": {
    "declaration": {
      "enabled": true,
      "fields": []
    }
  }
}
```

### Nie znaleziono szablonu HTML

Objaw:

```text
Nie znaleziono szablonu dokumentu w Nextcloud
```

Najczęstsze przyczyny:

- plik HTML nie istnieje w Nextcloud,
- błędna wielkość liter w nazwie katalogu,
- spacja na początku ścieżki,
- plik ma rozszerzenie `.html.txt` zamiast `.html`.

### Umowa się nie generuje

Sprawdź:

```json
"agreement": {
  "enabled": true,
  "template": "Template/umowa-wiedza-kluczem.html"
}
```

oraz czy deklaracja została poprawnie podpisana:

```text
declaration_signature_valid = Tak
```

Umowa nie wygeneruje się, jeżeli:

```text
agreement_blocked = Tak
```

### Podpis nie jest rozpoznany

Sprawdź, czy PDF został faktycznie podpisany elektronicznie, a nie tylko wygenerowany jako zwykły PDF.

---

## 14. Pełny skrócony przykład sekcji `process`

```json
"process": {
  "documents": {
    "declaration": {
      "enabled": true,
      "template": "Template/deklaracja-wiedza-kluczem.html",
      "filename_pattern": "{first_name}_{last_name}-deklaracja.pdf",
      "signature_required": true,
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
