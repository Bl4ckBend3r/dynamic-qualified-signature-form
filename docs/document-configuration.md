# Konfiguracja deklaracji i umów z poziomu Nextcloud

Dokumenty projektowe są konfigurowane w definicji formularza JSON przechowywanej w Nextcloud.

Dzięki temu każdy formularz może mieć własny zestaw dokumentów lub nie używać ich wcale.

---

## Założenia

- Formularz JSON decyduje, czy deklaracja jest wymagana.
- Formularz JSON decyduje, czy umowa jest wymagana.
- Szablony dokumentów mogą być przechowywane w Nextcloud.
- Jeżeli dokument nie jest włączony, system nie powinien wymuszać jego generowania ani podpisywania.
- Jeżeli dokument jest włączony, system używa wskazanego szablonu albo szablonu domyślnego aplikacji.

---

## Przykładowa konfiguracja formularza z deklaracją i umową

```json
{
  "title": "Formularz zgłoszeniowy",
  "description": "Formularz zgłoszenia uczestnika do programu.",
  "process": {
    "documents": {
      "declaration": {
        "enabled": true,
        "template": "templates/deklaracja.html",
        "filename_pattern": "{first_name}_{last_name}-deklaracja.pdf",
        "signature_required": true
      },
      "agreement": {
        "enabled": true,
        "template": "templates/umowa.html",
        "filename_pattern": "{first_name}_{last_name}-umowa.pdf",
        "signature_required": true
      }
    }
  },
  "fields": []
}
```

---

## Przykładowa konfiguracja formularza bez deklaracji i bez umowy

```json
{
  "title": "Prosty formularz kontaktowy",
  "description": "Formularz niewymagający dokumentów do podpisu.",
  "process": {
    "documents": {
      "declaration": {
        "enabled": false
      },
      "agreement": {
        "enabled": false
      }
    }
  },
  "fields": []
}
```

---

## Przykładowa konfiguracja tylko z deklaracją

```json
{
  "title": "Formularz z deklaracją",
  "process": {
    "documents": {
      "declaration": {
        "enabled": true,
        "template": "templates/deklaracja.html",
        "filename_pattern": "{first_name}_{last_name}-deklaracja.pdf",
        "signature_required": true
      },
      "agreement": {
        "enabled": false
      }
    }
  },
  "fields": []
}
```

---

## Ścieżki szablonów

Jeżeli ścieżka szablonu nie zaczyna się od katalogu `Formularze/` ani `output/`, system szuka jej względem katalogu formularzy w Nextcloud.

Przykład:

```json
{
  "template": "templates/deklaracja.html"
}
```

Przy domyślnej konfiguracji oznacza plik:

```text
Formularze/templates/deklaracja.html
```

Można też podać pełną ścieżkę w przestrzeni Nextcloud:

```json
{
  "template": "Formularze/templates/deklaracja.html"
}
```

---

## Dostępne zmienne w szablonie HTML

Szablon HTML może korzystać ze zmiennych Jinja:

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

Przykład użycia danych uczestnika:

```html
<p>Uczestnik: {{ participant_name }}</p>
<p>PESEL: {{ submission.get("pesel", "") }}</p>
<p>Email: {{ submission.get("email", "") }}</p>
```

---

## Nazwa pliku dokumentu

Pole `filename_pattern` obsługuje następujące zmienne:

```text
{first_name}
{last_name}
{participant_name}
{submission_id}
```

Przykład:

```json
{
  "filename_pattern": "{first_name}_{last_name}-deklaracja.pdf"
}
```

Jeżeli `filename_pattern` nie zostanie podany, system używa nazw domyślnych:

```text
Imie_Nazwisko-deklaracja.pdf
Imie_Nazwisko-umowa.pdf
```

---

## Pola zapisywane w tabeli eBiura

Przy wysłaniu formularza system zapisuje, czy dokumenty są wymagane:

```text
declaration_required
agreement_required
```

Dla formularza bez deklaracji i bez umowy wartości będą ustawione na:

```text
declaration_required = Nie
agreement_required = Nie
```

Dla formularza z deklaracją:

```text
declaration_required = Tak
```

Dla formularza z umową:

```text
agreement_required = Tak
```

---

## Aktualny zakres implementacji

Zaimplementowane:

- odczyt konfiguracji `process.documents` z JSON formularza,
- włączanie i wyłączanie deklaracji per formularz,
- włączanie i wyłączanie umowy per formularz na poziomie statusów procesu,
- generowanie deklaracji z domyślnego szablonu aplikacji,
- generowanie deklaracji z własnego szablonu HTML z Nextcloud,
- własny wzorzec nazwy pliku deklaracji,
- zapis informacji `declaration_required` i `agreement_required` do tabeli eBiura.

Do wykonania w kolejnym etapie:

- generowanie umowy z własnego szablonu HTML z Nextcloud,
- upload i weryfikacja podpisanej umowy,
- reguły blokujące wygenerowanie umowy.
