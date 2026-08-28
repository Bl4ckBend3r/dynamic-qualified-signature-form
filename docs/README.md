# Mapa dokumentacji

Dokumentacja jest pogrupowana według obszaru odpowiedzialności. Główny opis
projektu i szybki start pozostają w [`README.md`](../README.md).

## Uruchomienie i wdrożenie

- [`P3_OPERATIONS.md`](P3_OPERATIONS.md) — obserwowalność, backup/restore,
  test DR, wydajność i konkurencja dla PostgreSQL/MariaDB.
- [`deployment/production.md`](deployment/production.md) — wdrożenie produkcyjne
  z Docker Compose.
- [`deployment/STRICT_MODE_ROLLOUT.md`](deployment/STRICT_MODE_ROLLOUT.md) —
  uruchamianie i wycofywanie strict mode.

## Formularze i dokumenty

- [`forms/document-configuration.md`](forms/document-configuration.md) — model
  konfiguracji dokumentów w JSON.
- [`forms/json-and-html-templates.md`](forms/json-and-html-templates.md) — tworzenie
  definicji formularzy i szablonów HTML.
- [`forms/instrukcja-deklaracje-i-umowy.md`](forms/instrukcja-deklaracje-i-umowy.md)
  — instrukcja redakcyjna deklaracji i umów.

## Workflow

- [`workflow/process-workflow.md`](workflow/process-workflow.md) — techniczny
  przebieg procesu obsługi zgłoszenia.
- [`workflow/workflow-dla-osob-nietechnicznych.md`](workflow/workflow-dla-osob-nietechnicznych.md)
  — opis workflow dla osób nietechnicznych.

## Administracja

- [`testing/test-reczny-csrf-logowania.md`](testing/test-reczny-csrf-logowania.md) —
  ręczny test ochrony CSRF logowania.

## Archiwum i raporty

- `archive/` — historyczne plany i raporty zachowane wyłącznie jako kontekst.
- `reports/` — raporty z jednorazowych audytów i prac utrzymaniowych.
- [`database/P4_SCHEMA_CHECK.md`](database/P4_SCHEMA_CHECK.md) — kontrola schematu
  przed operacjami P4.

Puste katalogi tematyczne są utrzymywane przez `.gitkeep`, aby docelowa mapa
dokumentacji pozostała przewidywalna.
