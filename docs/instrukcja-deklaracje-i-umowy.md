# Instrukcja przygotowywania deklaracji i umów

Ten dokument jest przeznaczony dla osób przygotowujących treść deklaracji, oświadczeń i umów w HTML.

Techniczna konfiguracja JSON jest opisana w:

```text
docs/document-configuration.md
docs/json-and-html-templates.md
```

---

## Aktualny przebieg dla uczestnika

1. Uczestnik wypełnia formularz zgłoszeniowy.
2. Urzędnik akceptuje zgłoszenie.
3. Uczestnik przechodzi na stronę dokumentów do podpisania i wpisuje numer zgłoszenia.
4. Uczestnik uzupełnia deklarację.
5. System generuje deklarację PDF.
6. Uczestnik podpisuje deklarację zewnętrznie, na przykład Profilem Zaufanym albo mSzafirem.
7. Uczestnik wgrywa podpisaną deklarację.
8. System weryfikuje podpis deklaracji.
9. System generuje umowę albo umowy szkoleniowe.
10. Uczestnik pobiera umowę PDF.
11. Uczestnik podpisuje umowę zewnętrznie.
12. Uczestnik wgrywa podpisaną umowę.
13. System weryfikuje podpis umowy.
14. Umowa może zostać przekazana do późniejszego podpisu przez urząd.

Ważne: umowa jest najpierw podpisywana przez uczestnika/osobę prywatną. Podpis urzędu jest kolejnym, późniejszym etapem.

---

## Zasady ogólne dla dokumentów

1. Jeden plik HTML powinien zawierać kompletną treść dokumentu.
2. Dokument powinien mieć jasny tytuł i logiczne sekcje.
3. Nie formatuj treści spacjami, tabulatorami ani wieloma pustymi liniami.
4. Wygląd powinien wynikać z CSS i klas, a nie z ręcznego ustawiania tekstu.
5. W treści używaj zmiennych Jinja tylko tam, gdzie mają pojawić się dane z formularza.
6. Nie wpisuj ręcznie danych uczestnika, numeru zgłoszenia ani dat generowanych przez system.
7. Szablony przechowuj w Nextcloud, zwykle w katalogu `Template/`.

---

## Dozwolone znaczniki HTML

Zalecane:

```text
article
header
section
h1
h2
h3
p
div
span
ul
ol
li
table
thead
tbody
tr
th
td
strong
em
```

Do łamania wiersza w pojedynczym bloku można użyć `br`, ale nie używaj go do budowania układu całego dokumentu.

Unikaj:

```text
font
center
```

---

## Klasy główne

```text
document
document--declaration
document--agreement
document--form
document-header
document-title
document-subtitle
document-meta
```

Przykład:

```html
<body class="document document--declaration">
  <h1 class="document-title">Deklaracja uczestnictwa</h1>
</body>
```

---

## Klasy sekcji

```text
document-section
document-section--compact
document-section--page-break
section-title
section-subtitle
section-note
```

---

## Klasy pól

```text
field-grid
field-grid--two
field-grid--three
field
field--full
field-label
field-value
field-help
```

Przykład:

```html
<section class="document-section">
  <h2 class="section-title">Dane uczestnika</h2>
  <div class="field-grid field-grid--two">
    <div class="field">
      <span class="field-label">Imię i nazwisko</span>
      <span class="field-value">{{ participant_name }}</span>
    </div>
    <div class="field">
      <span class="field-label">PESEL</span>
      <span class="field-value">{{ submission.get("pesel", "") }}</span>
    </div>
  </div>
</section>
```

---

## Klasy odpowiedzi i oświadczeń

```text
choice-group
choice-group--inline
choice-item
checkbox
checkbox--checked
choice-label
statement-list
statement-item
statement-number
statement-text
statement-note
```

Przykład odpowiedzi TAK/NIE:

```html
<td class="answer">
  {% if submission.get("deklaracja_18_lat") == "Tak" %}●{% else %}○{% endif %}
</td>
<td class="answer">
  {% if submission.get("deklaracja_18_lat") == "Nie" %}●{% else %}○{% endif %}
</td>
```

---

## Klasy podpisów

```text
signature-area
signature-grid
signature-block
signature-line
signature-space
signature-label
signature-date
```

Przykład:

```html
<section class="signature-area">
  <div class="signature-grid">
    <div class="signature-block">
      <span class="signature-line"></span>
      <span class="signature-label">Uczestnik projektu</span>
    </div>
    <div class="signature-block">
      <span class="signature-line"></span>
      <span class="signature-label">Beneficjent / urząd</span>
    </div>
  </div>
</section>
```

---

## Klasy tabel i przypisów

```text
document-table
document-table--compact
document-table--bordered
table-note
footnotes
footnote
legal-note
small-note
```

---

## Minimalna struktura deklaracji

```html
<!doctype html>
<html lang="pl">
<head>
  <meta charset="UTF-8">
  <title>Deklaracja uczestnictwa</title>
</head>
<body class="document document--declaration">
  <header class="document-header">
    <h1 class="document-title">Deklaracja uczestnictwa</h1>
    <p class="document-subtitle">Dokument wygenerowany na podstawie formularza zgłoszeniowego.</p>
  </header>

  <section class="document-section">
    <h2 class="section-title">Dane uczestnika</h2>
    <p>Uczestnik: <strong>{{ participant_name }}</strong></p>
    <p>PESEL: <strong>{{ submission.get("pesel", "") }}</strong></p>
  </section>

  <section class="document-section">
    <h2 class="section-title">Oświadczenia</h2>
    <p>Oświadczam, że dane podane w formularzu są zgodne ze stanem faktycznym.</p>
  </section>

  <p class="small-note">ID zgłoszenia: {{ submission_id }}</p>
</body>
</html>
```

---

## Minimalna struktura umowy

```html
<!doctype html>
<html lang="pl">
<head>
  <meta charset="UTF-8">
  <title>Umowa uczestnictwa</title>
</head>
<body class="document document--agreement">
  <header class="document-header">
    <h1 class="document-title">
      Umowa uczestnictwa nr {{ agreement_number|default(submission_id, true) }}
    </h1>
  </header>

  <section class="document-section">
    <h2 class="section-title">Strony umowy</h2>
    <p>Uczestnik: <strong>{{ participant_name }}</strong></p>
    <p>PESEL: <strong>{{ submission.get("pesel", "") }}</strong></p>
  </section>

  {% if training_name %}
    <section class="document-section">
      <h2 class="section-title">Szkolenie</h2>
      <p>{{ training_name }}</p>
    </section>
  {% endif %}

  <section class="signature-area">
    <div class="signature-grid">
      <div class="signature-block">
        <span class="signature-line"></span>
        <span class="signature-label">Uczestnik projektu</span>
      </div>
      <div class="signature-block">
        <span class="signature-line"></span>
        <span class="signature-label">Beneficjent / urząd</span>
      </div>
    </div>
  </section>
</body>
</html>
```

---

## Wybrane szkolenia w deklaracji lub umowie

Jeżeli formularz pozwala wybrać szkolenia, w szablonie można użyć:

```html
{% if selected_trainings %}
  <section class="document-section">
    <h2 class="section-title">Wybrane szkolenia</h2>
    <ol>
      {% for training in selected_trainings %}
        <li>
          {{ training.get("name") }}
          {% if training.get("price") %}
            - {{ training.get("price") }} PLN
          {% endif %}
        </li>
      {% endfor %}
    </ol>
  </section>
{% endif %}
```

---

## Zmienne, których warto używać

```text
{{ submission_id }}
{{ participant_name }}
{{ submission.get("imie", "") }}
{{ submission.get("imiona", "") }}
{{ submission.get("nazwisko", "") }}
{{ submission.get("pesel", "") }}
{{ submission.get("email", "") }}
{{ selected_trainings }}
{{ training_name }}
{{ agreement_number }}
{{ generated_date }}
```

Nie zakładaj, że każde pole zawsze istnieje. Bezpieczny zapis to:

```html
{{ submission.get("nazwa_pola", "") }}
```

---

## Template maila z instrukcją dla uczestnika

Ten szablon może być zapisany w Nextcloud jako:

```text
Template/Mail/decision_accepted.html
```

albo pod inną nazwą wskazaną w konfiguracji powiadomień.

```html
<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <title>Wniosek zaakceptowany - instrukcja podpisania dokumentów</title>
</head>
<body style="font-family: Arial, sans-serif; color: #111827; line-height: 1.5;">
  <h2>Wniosek zaakceptowany</h2>

  <p>Dzień dobry,</p>

  <p>
    Twój wniosek w formularzu <strong>{{ form_title }}</strong> został zaakceptowany.
    Aby kontynuować udział w projekcie, wykonaj poniższe kroki.
  </p>

  <p>
    <strong>Formularz:</strong> {{ form_title }}<br>
    <strong>Numer zgłoszenia:</strong> {{ submission_id }}<br>
    <strong>Status:</strong> zaakceptowany
  </p>

  <h3>Instrukcja</h3>

  <ol>
    <li>Przejdź na stronę projektu.</li>
    <li>Otwórz sekcję <strong>Do podpisania</strong>.</li>
    <li>Wpisz swój numer zgłoszenia: <strong>{{ submission_id }}</strong>.</li>
    <li>Zaznacz wymaganą akceptację dokumentów, jeżeli pojawi się na stronie.</li>
    <li>Przejdź do obsługi dokumentów.</li>
    <li>Wypełnij deklarację uczestnictwa.</li>
    <li>Kliknij <strong>Wygeneruj deklarację PDF</strong>.</li>
    <li>Pobierz wygenerowaną deklarację PDF.</li>
    <li>Przejdź na stronę Profilu Zaufanego lub mSzafir i podpisz deklarację.</li>
    <li>Pobierz podpisaną deklarację.</li>
    <li>Wróć na stronę <strong>Do podpisania</strong>.</li>
    <li>Wgraj podpisaną deklarację PDF.</li>
    <li>Kliknij <strong>Wyślij podpisaną deklarację</strong>.</li>
    <li>Po pozytywnej weryfikacji pobierz umowę PDF.</li>
    <li>Podpisz umowę zewnętrznie, tak jak deklarację.</li>
    <li>Pobierz podpisaną umowę.</li>
    <li>Wróć na stronę <strong>Do podpisania</strong>.</li>
    <li>Wgraj podpisaną umowę PDF.</li>
    <li>Kliknij <strong>Wyślij podpisaną umowę</strong>.</li>
    <li>Po poprawnej weryfikacji umowa zostanie przekazana do dalszej obsługi i podpisu przez urząd.</li>
  </ol>

  <p>
    W razie problemów skontaktuj się z obsługą projektu i podaj numer zgłoszenia:
    <strong>{{ submission_id }}</strong>.
  </p>

  <p>Pozdrawiamy</p>
</body>
</html>
```

---

## Checklist przed publikacją

1. Plik HTML jest zapisany w Nextcloud w katalogu `Template/` albo `Template/Mail/`.
2. Dokument ma jeden główny tytuł.
3. Dane uczestnika pochodzą ze zmiennych, a nie z ręcznie wpisanego tekstu.
4. Deklaracja zawiera wszystkie wymagane pola i oświadczenia.
5. Umowa zawiera miejsce na podpis uczestnika i urzędu, jeżeli wymaga tego wzór.
6. Numer umowy używa `agreement_number`, jeżeli jest konfigurowany.
7. Data umowy używa `generated_date` albo `agreement_generated_at`.
8. Lista szkoleń używa `selected_trainings` albo danych `training_*`.
9. Dokument został sprawdzony po wygenerowaniu PDF.
10. Mail instrukcyjny mówi jasno, że uczestnik podpisuje deklarację i umowę, a urząd podpisuje umowę później.
