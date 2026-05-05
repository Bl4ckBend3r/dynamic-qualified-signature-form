# Dynamiczny formularz Flask z PDF, CSV i procesem kwalifikowanego podpisu

Aplikacja webowa w Pythonie umożliwiająca:

- wczytanie definicji formularza z pliku JSON,
- dynamiczne renderowanie formularza w HTML,
- walidację danych po stronie backendu,
- generowanie dokumentów PDF,
- zapis danych formularza do pliku CSV,
- obsługę dokumentów projektowych: deklaracji i umów,
- weryfikację podpisów elektronicznych dokumentów,
- przechowywanie plików PDF oraz podpisanych wersji dokumentów.

## Dokumentacja

Szczegółowa instrukcja tworzenia plików JSON formularzy oraz szablonów HTML dokumentów znajduje się w pliku:

```text
docs/json-and-html-templates.md
```

Instrukcja opisuje:

- strukturę pliku JSON formularza,
- konfigurację deklaracji i umowy,
- pola uzupełniane przez uczestnika w deklaracji,
- szablony HTML deklaracji i umowy,
- zmienne dostępne w HTML,
- najczęstsze błędy konfiguracji.

## Założenie dotyczące podpisu

Aplikacja **nie implementuje odręcznego podpisu**, pola typu handwritten signature ani podpisu rysowanego myszką.

Podpis elektroniczny jest traktowany jako **zewnętrzny proces podpisywania całego dokumentu PDF**.

Dopuszczalne podpisy dokumentów projektowych:

- mSzafir,
- Profil Zaufany.

## Wymagania systemowe

### Python

- Python 3.10 lub nowszy

### Playwright

Aplikacja generuje PDF z HTML przy użyciu Playwright.

Po instalacji zależności Python należy uruchomić:

```bash
python -m playwright install chromium
```

## Podstawowy przebieg procesu

1. Uczestnik wypełnia formularz online.
2. System zapisuje dane i generuje PDF formularza zgłoszeniowego.
3. Urzędnik akceptuje albo odrzuca zgłoszenie w tabeli eBiura.
4. Po akceptacji uczestnik przechodzi do dokumentów do podpisania.
5. Jeżeli formularz wymaga deklaracji, uczestnik uzupełnia pola deklaracji na stronie.
6. System generuje deklarację PDF na podstawie formularza zgłoszeniowego i danych deklaracji.
7. Uczestnik podpisuje deklarację elektronicznie i wgrywa podpisany PDF.
8. System weryfikuje podpis deklaracji.
9. Jeżeli warunki są spełnione, system generuje umowę PDF automatycznie na podstawie przesłanych danych.
10. Uczestnik podpisuje umowę elektronicznie i wgrywa podpisany PDF.
11. System weryfikuje podpis umowy.

## Konfiguracja plików formularzy i dokumentów

Formularze JSON oraz szablony HTML dokumentów są przechowywane w Nextcloud.

Przykładowa struktura:

```text
Strona WWW/
└── Formularze/
    ├── sample_form.json
    └── Template/
        ├── deklaracja-wiedza-kluczem.html
        └── umowa-wiedza-kluczem.html
```

W pliku JSON formularza ścieżki szablonów można podawać względem katalogu formularzy:

```json
"template": "Template/deklaracja-wiedza-kluczem.html"
```

Pełny opis konfiguracji znajduje się w:

```text
docs/json-and-html-templates.md
```
