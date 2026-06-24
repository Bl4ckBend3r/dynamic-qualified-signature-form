# Legacy removal migration plan

To jest plan przyszlej migracji, a nie migracja. P4.6 nie tworzy migracji Alembic, nie usuwa kolumn i nie usuwa kodu fallbackow.

## Zasady wejscia

- Wykonac backup bazy i storage.
- Uruchomic `scripts/check_p4_schema.py --report output/p4_schema_check.json` i potwierdzic zgodny schemat.
- Uruchomic `scripts/check_legacy_fallback_readiness.py --area documents|workflow|decisions`.
- Uruchomic `scripts/report_strict_mode_stabilization.py --area all`.
- Potwierdzic brak logow strict dla obszaru.
- Utrzymac rollback przez przywrocenie backupu i poprzedniej wersji aplikacji.
- Wykonac migracje destrukcyjna jako osobny etap po akceptacji.

## Pola legacy potencjalnie do usuniecia

| Pole | Warunek usuniecia | Zaleznosci |
| --- | --- | --- |
| `pdf_filename` | Dokument formularza ma `SubmissionFile` i stabilny `storage_path`. | Download, widok dokumentow, audyt plikow. |
| `signed_pdf_filename` | Podpisany formularz ma `SubmissionFile` i stabilny status podpisu. | Walidacja podpisu, download. |
| `declaration_*` | Deklaracje sa w `SubmissionFile`, statusy deklaracji maja docelowe odpowiedniki. | Workflow deklaracji, podpis deklaracji. |
| `agreement_*` | Umowy sa w `SubmissionFile`, statusy umow maja docelowe odpowiedniki. | Workflow umow, podpis umow. |
| `selected_trainings` | Wybor szkolen ma docelowa reprezentacje poza tekstowym polem legacy. | Generowanie umow szkoleniowych. |
| `training_agreements` | Umowy szkoleniowe sa w metadanych dokumentow i docelowej strukturze szkolen. | `SubmissionFile.training_key`, podpisy. |
| `signature_status` | Status podpisu jest w metadanych dokumentu albo osobnym statusie procesu. | Glowne PDF-y i podpisy. |
| `signature_request_id` | Identyfikatory zadan podpisu nie sa potrzebne w legacy kolumnie. | Provider podpisu. |
| `acceptance_required` | Wymagalnosc akceptacji ma docelowa reprezentacje workflow/rules. | Reguly i maile akceptacji. |
| `acceptance_email_sent` | Historia wysylki jest w logach maili. | `EmailLog`. |
| `decision_email_sent` | Historia maili decyzji jest w `EmailLog`. | Decyzje urzednika. |
| `decision_email_sent_for` | Odbiorca/typ maila decyzji jest odtwarzalny z `EmailLog`. | `EmailLog`. |
| `akceptacja` | Decyzja uczestnika ma docelowy audyt albo status. | Workflow i dokumenty po akceptacji. |
| `osw_*` | Oswiadczenia maja docelowa reprezentacje danych formularza lub zostaja celowo zachowane. | Wymogi formalne i audyt zgody. |
| historyczne pola deklaracji | Nie sa uzywane przez aktywny runtime albo maja docelowe odpowiedniki. | Dokumenty i workflow. |

## Pola, ktore moga zostac

- Dane formularza wymagane do wyswietlania, audytu i dokumentow.
- Pola statusowe, ktore nadal sa zrodlem prawdy procesu.
- Pola wymagane przez raporty administracyjne.
- Pola bez bezpiecznego docelowego odpowiednika.

## Kolejnosc migracji

1. Dokumenty: usunac zaleznosci od `pdf_filename`, `signed_pdf_filename`, `declaration_*`, `agreement_*`, `training_agreements` dopiero po stabilnym strict.
2. Workflow: usunac zaleznosci od historycznych statusow tylko po pelnym `SubmissionWorkflowEvent`.
3. Decyzje: usunac zaleznosci od pol decyzji legacy po stabilnym `SubmissionDecision` i `EmailLog`.
4. Formularz i oswiadczenia: osobna analiza wymogow prawnych i raportowych.
5. Cleanup kodu fallbackow po migracji schematu i testach regresji.

## Ryzyka

- Stare rekordy bez backfillu moga utracic mozliwosc pobrania dokumentow.
- Raporty administracyjne moga nadal czytac historyczne pola.
- `legacy_app.py` i testy zgodnosci moga oczekiwac starych pol.
- Rollback po usunieciu kolumn wymaga backupu, nie tylko zmiany flagi.

## Rollback

Rollback po destrukcyjnej migracji wymaga:

- backupu bazy sprzed migracji,
- backupu storage,
- poprzedniej wersji aplikacji,
- potwierdzenia zgodnosci migracji w dol, jesli taka migracja zostanie przygotowana.

## Testy przed migracja

- Pelny `pytest`.
- Readiness per obszar z kodem `0`.
- Stabilization report z `ready_for_legacy_removal`.
- Testy downloadu dokumentow.
- Testy workflow i decyzji.
- Testy maili decyzji i `EmailLog`.
- Testy runtime legacy importow.

## Testy po migracji

- Pelny `pytest`.
- Testy admina.
- Testy publicznych formularzy.
- Testy dokumentow, podpisow i downloadu.
- Testy raportow i backfillu w trybie zgodnosci.
- Smoke test produkcyjnych URL-i.
