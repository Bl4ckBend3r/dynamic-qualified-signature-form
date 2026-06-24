# Legacy removal checklist

P4.6 nie usuwa pol legacy, nie usuwa `legacy_app.py` i nie wykonuje destrukcyjnej migracji. Ten dokument jest lista warunkow wejscia do osobnego etapu legacy cleanup.

## Dokumenty

- `scripts/check_legacy_fallback_readiness.py --area documents` zwraca kod `0`.
- `STRICT_DOCUMENT_METADATA_READ=true` dziala stabilnie przez ustalony okres obserwacji.
- Brak logow `strict_document_metadata_missing`.
- `scripts/report_legacy_fallbacks.py` nie pokazuje uzycia `pdf_filename`, `signed_pdf_filename`, `declaration_*`, `agreement_*` ani `training_agreements` jako aktywnego fallbacku.
- Pobieranie dokumentow dziala przez `SubmissionFile.storage_path`.
- Brak nowych fallbackow po nazwie pliku.
- `SubmissionFile` pozostaje docelowa reprezentacja dokumentu.

## Workflow

- `scripts/check_legacy_fallback_readiness.py --area workflow` zwraca kod `0`.
- `STRICT_WORKFLOW_HISTORY_READ=true` dziala stabilnie.
- Brak logow `strict_workflow_events_missing`.
- Historia workflow pochodzi z `SubmissionWorkflowEvent`.
- Nie ma potrzeby skladania historii z `FormSubmission.process_status` i `FormSubmission.workflow_step`.

## Decyzje

- `scripts/check_legacy_fallback_readiness.py --area decisions` zwraca kod `0`.
- `STRICT_DECISION_AUDIT_READ=true` dziala stabilnie.
- Brak logow `strict_submission_decision_missing`.
- Decyzje pochodza z `SubmissionDecision`.
- Maile decyzji sa wysylane wylacznie przy faktycznej zmianie decyzji.
- `EmailLog` pozostaje osobnym logiem maili.

## Legacy app

- Potwierdzono, ze `legacy_app.py` nie jest produkcyjnym entrypointem.
- Runtime `create_app()` nadal nie importuje `legacy_app.py`.
- Testy zgodnosci legacy zostaly przepisane albo swiadomie zachowane.
- Historyczne endpointy w `legacy_app.py` sa oznaczone jako diagnostyczne albo usuwane w osobnym etapie.
- Decyzja o usunieciu pliku ma osobna akceptacje.

## Repo cleanup

- Artefakty nie sa fixture testow.
- `.coverage`, `.pytest_cache`, `__pycache__`, `tmp/logos` i `output` sa sprawdzone przed usunieciem.
- Cleanup ma osobny commit.
- Cleanup nie usuwa danych produkcyjnych ani lokalnych plikow potrzebnych do testow.
- Przed cleanupem pelny pakiet testow przechodzi.

## Gate

Do destrukcyjnej migracji wolno wejsc dopiero po zielonym schema check, zielonym raporcie stabilizacji strict mode, zielonych readiness checkach i potwierdzonym rollbacku. `schema_mismatch` blokuje legacy removal i wymaga migracji schematu przed backfillem oraz readiness. P4.6/P4.6.1 tego etapu nie wykonuje.
