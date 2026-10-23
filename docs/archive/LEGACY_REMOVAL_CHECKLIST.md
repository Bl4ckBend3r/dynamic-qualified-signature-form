# Lista kontrolna usuwania legacy

P4.6 nie usuwa automatycznie żadnego fallbacku. Najpierw wymagane są migracja danych, okres obserwacji strict mode i możliwość rollbacku.

## Dokumenty

- Backfill metadanych `SubmissionFile` jest zakończony i raport nie wykazuje braków.
- `strict_document_metadata_missing` nie pojawia się w okresie stabilizacji.
- Odczyty i zapisy przechodzą przez `DocumentService` oraz repozytorium plików.

## Workflow

- Każde zgłoszenie ma pełną historię `SubmissionWorkflowEvent`.
- Strict history read nie korzysta z syntetycznych zdarzeń.

## Decyzje

- Każda decyzja ma rekord `SubmissionDecision` i spójny snapshot typu decyzji.
- Strict decision audit nie odczytuje pól legacy zgłoszenia.

## Legacy app

- Runtime `create_app()` nie importuje `legacy_app.py`.
- Historyczne wrappery mają odpowiedniki w routes/services i pokrywające je testy.

## Repo cleanup

- Usuwane są wyłącznie zidentyfikowane artefakty lokalne, nie dane runtime.
- Po zmianie przechodzą testy obu obsługiwanych dialektów oraz pełny `pytest -q`.
