# Dynamiczny formularz Flask z PDF, CSV i procesem kwalifikowanego podpisu

Aplikacja webowa w Pythonie umożliwiająca:

- wczytanie definicji formularza z pliku JSON,
- dynamiczne renderowanie formularza w HTML,
- walidację danych po stronie backendu,
- generowanie dokumentu PDF,
- zapis danych formularza do pliku CSV,
- uruchomienie procesu kwalifikowanego podpisu elektronicznego przez warstwę abstrakcji,
- zapis metadanych procesu podpisu,
- przechowywanie pliku PDF oraz podpisanej wersji PDF.

## Założenie dotyczące podpisu

Aplikacja **nie implementuje odręcznego podpisu**, pola typu handwritten signature ani podpisu rysowanego myszką.

Podpis kwalifikowany jest traktowany jako **zewnętrzny proces podpisywania całego dokumentu PDF**.  
Warstwa `signature_service.py` udostępnia:

- interfejs `QualifiedSignatureProvider`,
- mock `MockQualifiedSignatureProvider` do testów lokalnych,
- szkielet `RestQualifiedSignatureProvider` do przyszłej integracji z rzeczywistym dostawcą przez REST API.

## Wymagania systemowe

### Python
- Python 3.10 lub nowszy

### Biblioteki systemowe dla WeasyPrint
Na Linux może być wymagane doinstalowanie zależności systemowych używanych przez WeasyPrint, np.:
- Pango
- Cairo
- GDK-PixBuf

Przykład dla Debian/Ubuntu:
```bash
sudo apt-get update
sudo apt-get install -y python3-dev build-essential libpango-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 libffi-dev shared-mime-info


# Instrukcja budowy formularza w pliku JSON

## Cel

Plik JSON definiuje strukturę formularza, jego nagłówek, pola wejściowe, sekcje oraz reguły warunkowego wyświetlania. Na podstawie tego pliku formularz jest renderowany dynamicznie w aplikacji.

---

## Struktura główna

Przykładowy schemat:

```json
{
  "title": "Tytuł formularza",
  "description": "Opis formularza wyświetlany pod nagłówkiem",
  "header_image": "forms/nazwa-pliku.png",
  "submit_label": "Generuj i wyślij",
  "fields": []
}
```

### Pola główne

- `title` – tytuł formularza wyświetlany w nagłówku.
- `description` – opcjonalny opis formularza pod tytułem i obrazem.
- `header_image` – opcjonalna ścieżka do obrazu w katalogu `static`, np. `forms/logo.png`.
- `submit_label` – tekst przycisku wysyłania.
- `fields` – lista pól formularza renderowanych w podanej kolejności.

---

## Zasady ogólne

- Każdy element formularza jest obiektem w tablicy `fields`.
- Kolejność obiektów w `fields` odpowiada kolejności renderowania w formularzu.
- Każde pole powinno posiadać `type`.
- Dla pól interaktywnych należy podać unikalne `name`.
- Szerokość pola określa parametr `width`.

Dostępne wartości `width`:

- `half` – pole o szerokości połowy wiersza
- `full` – pole o pełnej szerokości

---

## Typy pól

### 1. Sekcja

Służy do grupowania pól pod wspólnym nagłówkiem.

```json
{
  "type": "section",
  "label": "Dane kandydata / kandydatki"
}
```

Parametry:

- `type`: `section`
- `label`: tekst nagłówka sekcji

---

### 2. Tekst statyczny

Służy do wyświetlania informacji, instrukcji lub opisu w środku formularza.

```json
{
  "type": "static_text",
  "label": "Treść informacyjna wyświetlana w formularzu."
}
```

Parametry:

- `type`: `static_text`
- `label`: treść wyświetlana użytkownikowi

---

### 3. Pole tekstowe

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

Obsługiwane typy prostych pól wejściowych:

- `text`
- `email`
- `number`
- `date`
- `tel`
- `pesel`

Wspólne parametry:

- `type` – typ pola
- `name` – unikalna nazwa pola
- `label` – etykieta pola
- `placeholder` – tekst pomocniczy w polu
- `required` – czy pole jest wymagane (`true` / `false`)
- `readonly` – pole tylko do odczytu (`true` / `false`)
- `width` – `half` albo `full`
- `default` – wartość domyślna

---

### 4. Pole tekstowe wielowierszowe

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

Parametry:

- jak dla pól prostych

---

### 5. Lista rozwijana

```json
{
  "type": "select",
  "name": "wyksztalcenie",
  "label": "Wykształcenie",
  "required": true,
  "options": [
    "Brak",
    "Podstawowe",
    "Średnie",
    "Wyższe"
  ],
  "width": "half"
}
```

Parametry dodatkowe:

- `options` – lista dostępnych wartości

---

### 6. Radio

Pole jednokrotnego wyboru.

```json
{
  "type": "radio",
  "name": "plec",
  "label": "Płeć",
  "required": true,
  "options": ["Kobieta", "Mężczyzna", "Inna"],
  "width": "half"
}
```

Możliwe jest także dodanie opisu wyświetlanego nad opcjami:

```json
{
  "type": "radio",
  "name": "osoba_niepelnosprawna",
  "label": "Osoba z niepełnosprawnościami",
  "description": "Opis definicji lub dodatkowe objaśnienie pola.",
  "required": true,
  "options": ["Tak", "Nie", "Odmowa podania informacji"],
  "help_text": "Dane wrażliwe mogą być niezbędne do weryfikacji.",
  "width": "full"
}
```

Parametry dodatkowe:

- `options` – lista opcji wyboru
- `description` – opis wyświetlany nad grupą opcji
- `help_text` – dodatkowa pomoc wyświetlana pod polem

---

### 7. Checkbox

Pole wyboru stosowane głównie dla zgód i oświadczeń.

Zalecany model opiera się na `options`, nawet jeśli checkbox ma tylko jedną opcję.

```json
{
  "type": "checkbox",
  "name": "osw_rodo",
  "required": true,
  "width": "full",
  "options": [
    {
      "value": "Tak",
      "label": "Wyrażam zgodę na gromadzenie i przetwarzanie danych osobowych."
    }
  ]
}
```

Parametry:

- `type`: `checkbox`
- `name`: unikalna nazwa pola
- `required`: czy zaznaczenie jest wymagane
- `width`: `half` albo `full`
- `options`: lista obiektów opcji

Struktura pojedynczej opcji:

```json
{
  "value": "Tak",
  "label": "Treść zgody lub oświadczenia"
}
```

### Ważne

- Dla checkboxów zgód nie należy używać `label` jako nagłówka pola.
- Treść zgody powinna znajdować się w `options[].label`.
- Oznaczenie pola wymaganego (`*`) pojawia się przy treści opcji, a nie przy nagłówku.

---

## Wyświetlanie warunkowe pól

Możliwe jest warunkowe pokazanie pola zależnie od wartości innego pola.

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

Parametry `visible_if`:

- `field` – nazwa pola obserwowanego
- `operator` – operator porównania
- `value` – oczekiwana wartość

Obsługiwane operatory:

- `equals`
- `not_equals`

---

## Parametry opcjonalne wspólne dla wielu pól

W zależności od typu można używać:

- `required`
- `readonly`
- `placeholder`
- `default`
- `help_text`
- `description`
- `visible_if`
- `width`

---

## Obraz nagłówkowy formularza

Aby dodać obraz pod nazwą formularza, należy użyć pola `header_image`.

Przykład:

```json
{
  "title": "Projekt „Wiedza kluczem do sukcesu”",
  "description": "Formularz zgłoszeniowy...",
  "header_image": "forms/Logo-iwona.png",
  "submit_label": "Generuj i wyślij",
  "fields": []
}
```

Wymagania:

- plik musi znajdować się w katalogu `static/forms/`
- w JSON należy używać ścieżki względnej względem katalogu `static`
- przykład poprawnej ścieżki: `forms/Logo-iwona.png`

---

## Przykład kompletnego formularza

```json
{
  "title": "Projekt „Wiedza kluczem do sukcesu”",
  "description": "FORMULARZ ZGŁOSZENIOWY do projektu.",
  "header_image": "forms/Logo-iwona.png",
  "submit_label": "Generuj i wyślij",
  "fields": [
    {
      "type": "section",
      "label": "Dane podstawowe"
    },
    {
      "type": "text",
      "name": "imiona",
      "label": "Imię (imiona)",
      "placeholder": "Wpisz imię lub imiona",
      "required": true,
      "width": "half"
    },
    {
      "type": "text",
      "name": "nazwisko",
      "label": "Nazwisko",
      "required": true,
      "width": "half"
    },
    {
      "type": "radio",
      "name": "plec",
      "label": "Płeć",
      "required": true,
      "options": ["Kobieta", "Mężczyzna", "Inna"],
      "width": "half"
    },
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
  ]
}
```

---

## Zalecenia

- Stosować spójne nazwy pól `name`.
- Dla checkboxów zgód zawsze używać `options` jako listy obiektów.
- Dla pól wymaganych ustawiać `required: true`.
- Dłuższe objaśnienia dodawać w `description` lub `help_text`.
- Pola zależne od odpowiedzi definiować przez `visible_if`.
- Obrazy nagłówkowe umieszczać w `static/forms/`.

---

## Najczęstsze błędy

- użycie `options` jako string zamiast listy
- użycie `label` zamiast `options[].label` dla checkboxów zgód
- błędna ścieżka `header_image`
- brak `name` dla pola interaktywnego
- brak `width`
- literówka w `visible_if.field`
- niespójne wartości w `radio` i `visible_if.value`

---

## Rekomendowany model dla zgód

```json
{
  "type": "checkbox",
  "name": "osw_regulamin",
  "required": true,
  "width": "full",
  "options": [
    {
      "value": "Tak",
      "label": "Zapoznałem/am się z Regulaminem rekrutacji i uczestnictwa w Projekcie i akceptuję jego treść."
    }
  ]
}
```

Ten model należy stosować dla wszystkich oświadczeń i zgód w formularzu.
