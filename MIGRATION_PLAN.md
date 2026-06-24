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

Po P2.9 nie wykonano migracji. Elementy dokumentowe przygotowane do przyszlej migracji:

- `SubmissionFile.storage_path` pozostaje zrodlem prawdy dla odczytu plikow,
- legacy fallback po nazwie pliku pozostaje tylko dla starszych rekordow i jest logowany,
- dane `declaration_*`, `agreement_*`, `selected_trainings` i `training_agreements` nadal zostaja w `FormSubmission`,
- przyszla migracja moze przeniesc status dokumentu, wynik podpisu, numer umowy, `signed_filename` i `generated_at` do `SubmissionFile` albo `SubmissionDocument`,
- nie dodano nowych fallbackow po nazwie i nie zmieniono istniejacych sciezek storage.

Po P3.0 nadal nie wykonano migracji. Nowe serwisy dokumentowe nadal aktualizuja pola legacy dla zgodnosci:

- deklaracje: `declaration_required`, `declaration_generated`, `declaration_filename`, `process_status`,
- umowy: `agreement_generated`, `agreement_filename`, `agreement_generated_at`, `training_agreements`, `process_status`,
- podpisy dokumentow nadal uzywaja pol `declaration_signed*` oraz `agreement_signed*` w `FormSubmission`.

Przyszly etap migracyjny powinien wprowadzic dual-write do `SubmissionFile` albo `SubmissionDocument` dla statusu dokumentu, wyniku podpisu, numeru umowy i dat generowania. Backfill historycznych rekordow powinien odtworzyc metadane z obecnych pol legacy oraz istniejacych plikow bez usuwania kolumn w pierwszym kroku.

Po P3.1 nadal nie wykonano migracji. Refaktor legacy nie zmienia zapisu danych: nowe serwisy nadal aktualizuja pola legacy wymienione wyzej, a przyszly dual-write/backfill powinien objac takze metadane generowane obecnie przez orchestration dokumentow oraz istniejace rekordy bez `SubmissionFile.storage_path`.

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

## 7. Notatka P3.2 - dokumenty bez migracji

Etap P3.2 nie wprowadza migracji bazy. Nowe serwisy dokumentowe utrzymuja dual-write do istniejacych pol legacy i metadanych:

- `DocumentSigningService` dla podpisanego PDF-a glownego formularza nadal zapisuje `signed_pdf_filename`,
- ten sam zapis rejestruje metadane w `SubmissionFile` przez `record_submission_file()`,
- download nadal korzysta z `SubmissionFile.storage_path`, gdy metadane istnieja,
- fallback po nazwie pozostaje tylko dla starszych rekordow i nie zostal rozszerzony,
- publiczne URL-e dokumentow pozostaly bez zmian.

Przy przyszlej migracji dokumentow nalezy nadal traktowac `SubmissionFile.storage_path` jako docelowe zrodlo prawdy, a pola `pdf_filename`, `signed_pdf_filename`, `declaration_*`, `agreement_*` i `training_agreements` jako pola legacy do stopniowego backfillu.

## 8. Notatka P3.3 - flow deklaracji i umow bez migracji

Etap P3.3 nie wprowadza migracji bazy. Nowe flow nadal aktualizuja dotychczasowe pola legacy:

- deklaracje: `declaration_required`, `declaration_generated`, `declaration_filename`, `declaration_signed*`, `declaration_signature_*`,
- dodatkowe pola po akceptacji: pola formularza, `data_json`, `additional_fields_completed`, `process_status`, `workflow_step`,
- umowy: `agreement_generated`, `agreement_filename`, `agreement_generated_at`, `agreement_signed*`, `agreement_signature_*`,
- wiele umow szkoleniowych: `selected_trainings`, `training_agreements`.

W przyszlym dual-write/backfill nalezy objac co najmniej: status dokumentu, typ dokumentu, numer umowy, `generated_at`, `signed_filename`, wynik podpisu i powiazanie z wybranym szkoleniem. `SubmissionFile.storage_path` nadal pozostaje docelowym zrodlem prawdy dla lokalizacji pliku.

## 9. Notatka P3.4 - routing dokumentow bez migracji

Etap P3.4 nie zmienia zapisu danych i nie wprowadza migracji. `routes/documents.py` zostal formalnie zamkniety jako cienki modul HTTP, a serwisy nadal aktualizuja dotychczasowe pola legacy.

Bez zmian pozostaja:

- `FormSubmission` i pola `pdf_filename`, `signed_pdf_filename`, `declaration_*`, `agreement_*`, `selected_trainings`, `training_agreements`,
- zapis metadanych w `SubmissionFile`,
- `SubmissionFile.storage_path` jako docelowe zrodlo prawdy lokalizacji pliku,
- legacy fallback po nazwie tylko dla starszych rekordow.

## 10. Notatka P3.5 - legacy cleanup bez migracji

Etap P3.5 nie zmienia zapisu danych i nie wprowadza migracji. `legacy_app.py` zostal zachowany jako historyczny modul zgodnosci, ale runtime nadal aktualizuje pola przez nowe serwisy.

Bez zmian pozostaja:

- pola legacy `pdf_filename`, `signed_pdf_filename`, `declaration_*`, `agreement_*`, `selected_trainings`, `training_agreements`,
- zapis metadanych w `SubmissionFile`,
- `SubmissionFile.storage_path` jako zrodlo prawdy dla lokalizacji dokumentu,
- brak nowych fallbackow po nazwie pliku.

## 11. Notatka P3.6 - frontend dokumentow bez migracji

Etap P3.6 nie zmienia zapisu danych i nie wprowadza migracji. Przeniesienie inline CSS/JS z `templates/documents_to_sign.html` do statycznych assetow nie zmienia modeli, pol legacy ani sposobu zapisu dokumentow.

Bez zmian pozostaja:

- pola legacy `pdf_filename`, `signed_pdf_filename`, `declaration_*`, `agreement_*`, `selected_trainings`, `training_agreements`,
- zapis metadanych w `SubmissionFile`,
- `SubmissionFile.storage_path` jako zrodlo prawdy dla lokalizacji dokumentu,
- brak nowych fallbackow po nazwie pliku.

## 12. Etap P4.0 - struktury dual-write bez backfillu

Etap P4.0 wprowadza niedestrukcyjna migracje przygotowujaca odejscie od przechowywania dokumentow, workflow i decyzji bezposrednio w `FormSubmission`.

Dodane struktury:

- `SubmissionFile` pozostaje docelowa reprezentacja dokumentu; nie dodano osobnej tabeli `SubmissionDocument`,
- `SubmissionFile` dostaje metadane: `original_filename`, `signature_status`, `signature_validation_result`, `agreement_number`, `training_key`, `generated_at`, `signed_at`, `updated_at`,
- `submission_workflow_events` przechowuje historie zmian statusu/kroku,
- `submission_decisions` przechowuje audyt decyzji urzednika,
- migracja `20260610_0009_p4_dual_write_audit_structures.py` tworzy tylko nowe tabele/kolumny i indeksy.

Dual-write:

- stare pola legacy w `FormSubmission` nadal sa aktualizowane,
- nowe metadane dokumentow sa zapisywane przez `SubmissionDocumentService`,
- workflow zapisuje dodatkowo `SubmissionWorkflowEvent`,
- endpoint decyzji urzednika zapisuje dodatkowo `SubmissionDecision`,
- `EmailLog` pozostaje osobna tabela logow maili.

Bez zmian w P4.0:

- nie wykonano backfillu,
- nie usunieto pol legacy,
- nie zmieniono `SubmissionFile.storage_path` jako zrodla prawdy lokalizacji pliku,
- nie rozszerzono fallbacku po nazwie pliku,
- odczyt starszych rekordow nadal moze korzystac z legacy fallbackow.

Pola legacy pozostajace do kolejnych etapow:

- `pdf_filename`, `signed_pdf_filename`,
- `declaration_*`,
- `agreement_*`,
- `selected_trainings`, `training_agreements`,
- `signature_status`, `signature_request_id`,
- `acceptance_required`, `acceptance_email_sent`,
- `decision_email_sent`, `decision_email_sent_for`, `akceptacja`,
- pola `osw_*` i historyczne deklaracje.

## 13. Kolejny etap P4.1 - backfill

P4.1 powinien:

- wykonac backfill `SubmissionFile` dla historycznych dokumentow na podstawie istniejacych pol legacy,
- utworzyc poczatkowe eventy workflow tam, gdzie da sie je bezpiecznie odtworzyc,
- utworzyc audyty decyzji z istniejacych `officer_decision*`,
- przygotowac raport rekordow bez plikow/metadanych,
- dzialac na kopii bazy lub w kontrolowanej migracji, bez usuwania pol legacy.

## 14. Etap P4.1 - backfill metadanych legacy

Dodano skrypt `scripts/backfill_p4_metadata.py`, ktory uzupelnia nowe struktury z pol legacy bez usuwania ani nadpisywania legacy.

Zakres backfillu:

- `SubmissionFile` dla `pdf_filename`, `signed_pdf_filename`, `declaration_*`, `agreement_*` i `training_agreements`,
- poczatkowy `SubmissionWorkflowEvent` dla zgloszen bez eventow,
- `SubmissionDecision` dla historycznych decyzji, jezeli decyzje da sie odtworzyc z pol legacy,
- raport JSON z licznikami i bledami bez danych wrazliwych.

Zasady:

- domyslnie `dry-run`,
- zapis tylko z `--apply`,
- idempotencja dla dokumentow, workflow i decyzji,
- brak pliku jest raportowany, ale nie blokuje calosci,
- istniejace `SubmissionFile.storage_path` pozostaje zrodlem prawdy i nie jest nadpisywane,
- odczyt aplikacji nie zostal jeszcze globalnie przelaczony na nowe struktury,
- pola legacy nadal zostaja w `FormSubmission`.

## 15. Kolejny etap P4.2 - przelaczenie odczytu

P4.2 powinien:

- uruchomic P4.1 najpierw jako `--dry-run` na kopii bazy,
- po akceptacji raportu wykonac `--apply`,
- dopiero potem zaczac przelaczac odczyt dokumentow, workflow i decyzji na nowe struktury,
- zostawic pola legacy jako fallback do czasu stabilizacji,
- dodac testy starszych rekordow po backfillu i bez backfillu.

## 16. Etap P4.2 - odczyt preferuje nowe struktury

Etap P4.2 przelacza odczyt preferencyjnie na nowe struktury, ale nie usuwa pol legacy i nie wymaga, aby backfill byl wykonany w kazdym srodowisku.

Zakres:

- dokumenty sa budowane w widoku najpierw z `SubmissionFile`,
- download preferuje metadane dokumentu i `SubmissionFile.storage_path`,
- brak metadanych uruchamia dotychczasowy legacy fallback po nazwie pliku,
- historia workflow jest czytana z `SubmissionWorkflowEvent`, a przy braku eventow z `FormSubmission`,
- decyzje sa czytane z `SubmissionDecision`, a przy braku rekordu z pol legacy decyzji,
- fallbacki sa oznaczane w wyniku serwisow albo logowane.

Bez zmian:

- brak destrukcyjnej migracji,
- brak usuwania kolumn legacy,
- brak zmiany publicznych URL-i,
- brak runtime importu `legacy_app.py`,
- `EmailLog` pozostaje osobnym logiem maili.

## 17. Kolejny etap P4.3 - stabilizacja fallbackow

P4.3 powinien:

- monitorowac i raportowac uzycie fallbackow legacy,
- sprawdzic wyniki backfillu na kopii produkcji,
- ograniczac fallbacki tylko po potwierdzeniu kompletnego backfillu,
- pozostawic pola legacy do czasu osobnej migracji usuwajacej.

## 18. Etap P4.3 - raportowanie fallbackow legacy

Dodano obserwacje fallbackow bez ich wylaczania. Etap P4.3 nie usuwa pol legacy, nie zmienia odczytu publicznych endpointow i nie wykonuje migracji destrukcyjnej.

Zakres:

- `LegacyFallbackReportService` skanuje dokumenty, workflow i decyzje,
- `scripts/report_legacy_fallbacks.py` generuje raport konsolowy i opcjonalny JSON,
- raport pokazuje rekordy korzystajace z legacy fallbackow i braki w nowych metadanych,
- strict mode jest dostepny przez flagi konfiguracyjne, ale domyslnie wylaczony,
- fallbacki danych legacy w `FormSubmission` sa nadal aktywne.

## 19. Kolejny etap P4.4 - ograniczanie fallbackow

P4.4 powinien:

- wykorzystac raport P4.3 z kopii produkcji,
- ograniczac fallbacki tylko w obszarach z kompletnymi metadanymi,
- pozostawic kontrolowany rollback do pol legacy,
- nie usuwac kolumn legacy bez osobnej migracji i akceptacji.

## 20. Etap P4.4 - kontrolowane ograniczanie fallbackow

P4.4 dodaje readiness gate przed wlaczeniem strict mode. Fallbacki legacy nie sa globalnie wylaczone i pozostaja domyslnie aktywne.

Zakres:

- `STRICT_DOCUMENT_METADATA_READ` blokuje legacy odczyt dokumentu po nazwie, jezeli brakuje metadanych nowego `SubmissionFile`,
- `STRICT_WORKFLOW_HISTORY_READ` blokuje skladanie historii z `FormSubmission` i zwraca diagnostyczny wynik,
- `STRICT_DECISION_AUDIT_READ` blokuje odczyt decyzji z pol legacy, jezeli nie ma `SubmissionDecision`,
- `scripts/check_legacy_fallback_readiness.py` sprawdza gotowosc obszarow `documents`, `workflow` i `decisions`,
- readiness CLI zwraca `0` dla gotowosci, `1` dla blokerow i `2` dla bledow technicznych,
- raport readiness moze zostac zapisany przez `--report` i nie zawiera danych wrazliwych.

Zasady wlaczania strict:

- najpierw uruchomic readiness na kopii produkcji,
- wlaczac flagi osobno per obszar,
- nie wlaczac flagi, jezeli readiness zwroci kod `1`,
- przy kodzie `2` naprawic problem techniczny albo schemat bazy przed ponownym sprawdzeniem,
- nie usuwac pol legacy w ramach wlaczania strict.

## 21. Kolejny etap P4.5 - operacyjne wlaczenie strict

P4.5 powinien byc operacyjny, a nie destrukcyjny:

- uruchomic `scripts/check_legacy_fallback_readiness.py --area ...` dla kazdego obszaru,
- wlaczyc tylko te flagi strict, ktore maja zielony raport,
- monitorowac logi `strict_missing_*` i pobrania dokumentow,
- zostawic szybki rollback przez wylaczenie flagi,
- dopiero po stabilizacji przygotowac osobna decyzje o migracji usuwajacej legacy.

## 22. Etap P4.5 - operacyjne wlaczanie strict mode

P4.5 przygotowuje bezpieczny rollout strict mode bez usuwania fallbackow globalnie i bez usuwania pol legacy.

Zakres:

- walidacja startowa loguje aktywne flagi strict przez `strict_mode_enabled`,
- opcjonalna flaga `REQUIRE_STRICT_READINESS_CHECK=false` loguje bramke zewnetrznego readiness, ale nie skanuje calej bazy przy starcie,
- zdarzenia strict sa logowane jako `strict_document_metadata_missing`, `strict_workflow_events_missing`, `strict_submission_decision_missing`,
- readiness loguje `strict_readiness_blocker` dla obszarow z blokerami,
- `scripts/check_legacy_fallback_readiness.py --recommend` generuje rekomendacje rollout,
- `STRICT_MODE_ROLLOUT.md` opisuje readiness, kolejnosc wlaczania, monitoring i rollback.

Zasady:

- strict jest wlaczany operacyjnie per obszar,
- obszary nie musza byc gotowe jednoczesnie,
- fallbacki legacy nadal dzialaja po wylaczeniu flag,
- pola legacy w `FormSubmission` pozostaja,
- nie ma destrukcyjnej migracji.

## 23. Kolejny etap P4.6 - stabilizacja albo decyzja o migracji

Po P4.5 kolejnym krokiem jest stabilizacja produkcyjna strict mode albo osobna decyzja o destrukcyjnej migracji. Migracja usuwajaca pola legacy wymaga oddzielnego planu, oddzielnej akceptacji i potwierdzenia rollbacku.

## 24. Etap P4.6 - stabilizacja strict mode i przygotowanie legacy cleanup

P4.6 jest etapem przygotowawczym. Nie usuwa pol legacy, nie usuwa `legacy_app.py`, nie usuwa fallbackow i nie tworzy destrukcyjnej migracji.

Zakres:

- `StrictModeStabilizationService` raportuje stan dokumentow, workflow i decyzji po rollout strict,
- `scripts/report_strict_mode_stabilization.py` generuje raport JSON na stdout i opcjonalnie do pliku,
- raport rozroznia akcje `keep_fallback`, `enable_strict`, `stabilize`, `ready_for_legacy_removal`,
- `LEGACY_REMOVAL_CHECKLIST.md` opisuje warunki wejscia do migracji destrukcyjnej,
- `LEGACY_REMOVAL_MIGRATION_PLAN.md` opisuje przyszly plan usuwania pol legacy bez wykonania migracji,
- `LEGACY_APP_RETIREMENT_PLAN.md` opisuje warunki usuniecia albo pozostawienia `legacy_app.py`.

Zasady:

- obszar moze byc kandydatem do usuniecia fallbackow dopiero po aktywnym strict i zielonym readiness,
- brak stabilizacji oznacza dalszy backfill albo pozostawienie fallbacku,
- rollback strict nadal polega na wylaczeniu flagi,
- rollback po destrukcyjnej migracji wymaga backupu i osobnego planu.

## 25. Kolejny etap P4.7 - decyzja po stabilizacji

P4.7 powinien byc decyzja: dalsza stabilizacja albo przygotowanie osobnej migracji destrukcyjnej. Migracja usuwajaca legacy moze powstac dopiero po spelnieniu `LEGACY_REMOVAL_CHECKLIST.md` i akceptacji `LEGACY_REMOVAL_MIGRATION_PLAN.md`.

## 26. Etap P4.6.1 - wyrownanie schematu P4

P4.6.1 potwierdza, ze brak `submission_files.original_filename` wynika z niewyrownanego schematu albo niewykonanej migracji P4, a nie z potrzeby usuwania legacy.

Zakres:

- `scripts/check_p4_schema.py` sprawdza wymagane tabele i kolumny P4,
- raporty diagnostyczne wykonuja rollback po bledach SQL,
- `schema_mismatch` blokuje `ready_for_legacy_removal`,
- dokumentacja wskazuje kolejnosc: schema check -> migracje -> backfill -> readiness -> stabilization -> decyzja.

Nie dodano nowej migracji usuwajacej dane. Istniejaca migracja P4 `20260610_0009_p4_dual_write_audit_structures.py` zawiera brakujace kolumny `SubmissionFile`; srodowisko powinno wykonac `alembic upgrade head`.
