# Plan porządkowania danych zgłoszenia

## Pola do zostawienia

Identyfikatory, `form_slug`, `form_version_id`, dane kontaktowe oraz bieżące warstwowe pola stanu pozostają, dopóki używa ich runtime i raportowanie.

## Pola do przeniesienia do `data_json`

Jednorazowe, formularzowe wartości bez własnych relacji mogą być przechowywane w `FormSubmission.data_json`, o ile schema formularza określa ich znaczenie i walidację.

## Pola do osobnych tabel

Szkolenia należą do `SubmissionTraining`, pliki do `SubmissionFile`, decyzje do `SubmissionDecision`, zdarzenia do `SubmissionWorkflowEvent`, a dowody zgód do `SubmissionConsent`.

## Pola legacy do zachowania tymczasowo

`acceptance_required`, `decision_email_sent`, `akceptacja` i `signature_request_id` pozostają tylko jako kompatybilność odczytu do czasu zakończenia backfillu oraz obserwacji strict mode.

## Proponowane etapy migracji

1. Utworzyć schema przez liniową migrację Alembic.
2. Wykonać idempotentny backfill na kopii i porównać liczności.
3. Włączyć strict reads osobno dla dokumentów, workflow i decyzji.
4. Po okresie stabilizacji usunąć fallbacki, a później kolumny.

## Ryzyka

Największe ryzyka to niejednoznaczne umowy wielu szkoleń, brakujące pliki, brak historycznych dat akceptacji oraz częściowo wdrożony schema. Migracja ma przerywać pracę zamiast fabrykować dane audytowe.
