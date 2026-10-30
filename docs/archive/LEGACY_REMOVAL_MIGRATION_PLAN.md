# Plan migracji pól legacy

Plan opisuje kolejność odejścia od starych pól; nie tworzy migracji Alembic i nie stanowi zgody na usunięcie danych.

## Dokumenty i podpisy

Pola `pdf_filename`, `signed_pdf_filename`, `declaration_*`, `agreement_*`, `signature_status` i `signature_request_id` pozostają warstwą zgodności do czasu kompletnego backfillu do `SubmissionFile` oraz metadanych podpisu.

## Szkolenia i umowy

`selected_trainings` i `training_agreements` są lustrami tekstowymi. Canonical dane należą do `SubmissionTraining`, a umowa jest przypisana do jednego szkolenia.

## Decyzje i maile

`acceptance_required`, `acceptance_email_sent`, `decision_email_sent`, `decision_email_sent_for`, `akceptacja` i `osw_*` można usuwać dopiero po potwierdzeniu pokrycia przez `SubmissionDecision`, workflow i `EmailLog`.

## Etapy

1. Uruchomić kontrolę schematu i backfill na kopii bazy.
2. Włączyć odpowiedni strict read i obserwować raporty.
3. Usunąć odczyty fallback dopiero po zerowym raporcie braków.
4. Osobną migracją usunąć kolumny, mappery, formularze i testy zgodności.

## Rollback

Rollback wymaga kopii bazy sprzed DDL oraz zachowania raportu mapowania rekordów. Nie wolno odtwarzać pól z niejednoznacznych danych ani używać `alembic stamp` jako substytutu schematu.
