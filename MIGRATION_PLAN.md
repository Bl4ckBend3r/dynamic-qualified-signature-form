# Migration plan dla `FormSubmission`

Plan opisuje docelowe uporządkowanie modelu `FormSubmission` bez wykonywania migracji w tej iteracji. Celem jest przygotowanie aplikacji do wielu typów workflow i wielu formularzy bez utraty zgodności z istniejącymi zgłoszeniami.

## 1. Pola do zostawienia w `FormSubmission`

W `FormSubmission` powinny zostać pola opisujące samo zgłoszenie i umożliwiające szybkie wyszukiwanie:

- `id` — techniczny identyfikator rekordu.
- `submission_id` — publiczny identyfikator zgłoszenia używany w linkach i tokenach.
- `form_slug` — powiązanie ze źródłowym formularzem.
- `form_name` — historyczna nazwa formularza widoczna w zgłoszeniu.
- `email` — kontakt do wnioskodawcy, potrzebny do wyszukiwania i powiadomień.
- `imiona`, `nazwisko` albo docelowo znormalizowane pola kontaktowe, jeśli są używane w listach admina.
- `data_json` — dynamiczne dane formularza.
- `process_status` — aktualny status procesu.
- `workflow_step` — aktualny krok workflow.
- `access_token` — token dostępu do pobierania lub obsługi dokumentów.
- `created_at`, `updated_at` — znaczniki audytowe.
- relacja do `SubmissionFile` — pliki i dokumenty powinny być czytane przez metadane plików.

## 2. Pola do przeniesienia do `data_json`

Do `data_json` powinny trafić pola zależne od konkretnego formularza, których nie należy utrzymywać jako kolumn globalnych:

- pola deklaracji specyficzne dla jednego wzoru, np. `deklaracja_*`, jeśli nie sterują globalnym workflow,
- pola oświadczeń, np. `osw_*`,
- szczegółowe dane uczestnika, które nie są używane do indeksowania lub wyszukiwania,
- dynamiczne pola szkoleniowe,
- dodatkowe pola po akceptacji urzędnika,
- pola wynikające z konfiguracji JSON formularza,
- wartości pomocnicze potrzebne tylko do renderowania dokumentów.

Zasada: jeżeli pole pochodzi z definicji formularza i nie jest wspólnym atrybutem procesu, powinno trafić do `data_json`.

## 3. Pola do osobnych tabel

Następujące obszary powinny być modelowane osobno:

### Dokumenty

Docelowo dokumenty powinny być reprezentowane przez `SubmissionFile` albo nową tabelę `SubmissionDocument`:

- typ dokumentu,
- status dokumentu,
- informacja podpisany/niepodpisany,
- `storage_path`,
- nazwa pliku,
- data wygenerowania,
- data wgrania podpisu,
- wynik walidacji podpisu.

### Historia workflow

Dodać tabelę typu `SubmissionWorkflowEvent`:

- `submission_id`,
- poprzedni status,
- nowy status,
- poprzedni krok,
- nowy krok,
- aktor,
- powód,
- data zmiany.

### Decyzje urzędnika

Decyzję można docelowo przenieść do osobnej tabeli lub osobnego obiektu audytowego:

- decyzja,
- uzasadnienie,
- urzędnik,
- data decyzji,
- status docelowy,
- informacja o wysłanym mailu.

### Maile i logi

`EmailLog` powinien pozostać osobną tabelą. Nie należy przechowywać historii maili jako pól w `FormSubmission`.

### Szkolenia i umowy szkoleniowe

Dla wielu szkoleń korzystniejsze będzie wydzielenie:

- wybranego szkolenia,
- numeru umowy,
- kwoty,
- pliku umowy,
- statusu podpisu.

## 4. Pola legacy do zachowania tymczasowo

Nie usuwać bez migracji i testów historycznych danych:

- `acceptance_required`,
- `acceptance_email_sent`,
- `decision_email_sent`,
- `decision_email_sent_for`,
- `akceptacja`,
- `declaration_*`,
- `agreement_*`,
- `signature_status`,
- `signature_request_id`,
- `selected_trainings`,
- `training_agreements`,
- pola `osw_*`,
- pola specyficzne dla historycznej deklaracji.

Te pola powinny zostać oznaczone jako legacy i obsługiwane przez adapter do czasu zakończenia backfillu.

## 5. Proponowane etapy migracji

1. **Dodanie nowych struktur**
   - dodać brakujące tabele albo kolumny,
   - nie usuwać istniejących kolumn,
   - przygotować indeksy dla `submission_id`, `form_slug`, `process_status`.

2. **Dual-write**
   - przy nowych zapisach aktualizować jednocześnie stare pola i nowe struktury,
   - dodać testy porównujące spójność zapisu.

3. **Backfill danych**
   - przepisać historyczne pola formularzy do `data_json`,
   - utworzyć metadane dokumentów dla istniejących plików,
   - zapisać historię statusu początkowego tam, gdzie da się ją odtworzyć.

4. **Przełączenie odczytu**
   - najpierw odczytywać z nowych struktur,
   - stare pola traktować jako fallback legacy,
   - logować użycie fallbacków.

5. **Testy regresji**
   - sprawdzić istniejące zgłoszenia,
   - sprawdzić nowe zgłoszenia,
   - sprawdzić dokumenty podpisane i niepodpisane,
   - sprawdzić maile decyzji i workflow.

6. **Usunięcie pól legacy po stabilizacji**
   - dopiero po kilku iteracjach bez fallbacków,
   - wyłącznie przez migrację bazy,
   - z planem rollbacku.

## 6. Ryzyka

| Ryzyko | Opis | Ograniczenie |
| --- | --- | --- |
| Utrata danych | Pola historyczne mogą zawierać wartości niewystępujące w JSON. | Backfill tylko po kopii bazy i z raportem różnic. |
| Niespójność statusów | Stare statusy są mapowane przez katalog legacy. | Przed migracją używać `status_catalog.normalize_status()`. |
| Dokumenty bez metadanych | Starsze pliki mogą istnieć tylko jako ścieżki lub nazwy. | Utrzymać fallback legacy i stopniowo tworzyć `SubmissionFile`. |
| Historyczne zgłoszenia | Stare zgłoszenia mogą nie mieć kompletu pól workflow. | Dodawać wartości domyślne i oznaczać rekordy jako legacy. |
| Maile zależne od starych pól | Część szablonów może korzystać z `decision_email_sent` albo `agreement_*`. | Dodać adapter kontekstu maila i testy szablonów. |
| Regresja panelu admina | Listy i filtry mogą używać bezpośrednio pól legacy. | Przełączać odczyt stopniowo przez serwisy i view modele. |
