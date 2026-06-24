# Strict mode rollout

P4.5 opisuje operacyjne wlaczanie strict mode per obszar. Ten etap nie usuwa pol legacy, nie usuwa `legacy_app.py`, nie zmienia publicznych URL-i i nie wykonuje destrukcyjnych migracji.

## Zasada glowna

Strict mode wlaczaj tylko dla obszaru, ktory ma zielony readiness check. Obszary sa niezalezne:

- dokumenty: `STRICT_DOCUMENT_METADATA_READ`,
- workflow: `STRICT_WORKFLOW_HISTORY_READ`,
- decyzje: `STRICT_DECISION_AUDIT_READ`.

Domyslnie wszystkie flagi pozostaja wylaczone.

## Readiness

Dokumenty:

```powershell
python scripts/check_legacy_fallback_readiness.py --area documents --report output/readiness_documents.json
```

Workflow:

```powershell
python scripts/check_legacy_fallback_readiness.py --area workflow --report output/readiness_workflow.json
```

Decyzje:

```powershell
python scripts/check_legacy_fallback_readiness.py --area decisions --report output/readiness_decisions.json
```

Kod wyjscia:

- `0` - obszar gotowy, mozna wlaczyc strict dla tego obszaru,
- `1` - sa blokery, zostaw fallback,
- `2` - blad techniczny, napraw srodowisko lub schemat przed ponownym sprawdzeniem.

## Plan rollout

Mozesz wygenerowac rekomendacje dla wszystkich obszarow:

```powershell
python scripts/check_legacy_fallback_readiness.py --area all --recommend --report output/strict_rollout_plan.json
```

Rekomendacje:

- `enable_strict` - obszar gotowy,
- `keep_fallback` - obszar ma blokery albo bledy.

Skrypt nie zmienia konfiguracji, nie zapisuje danych w bazie i nie wysyla maili.

## Wlaczenie flag

Wlacz tylko wybrany obszar po kodzie wyjscia `0`:

```env
STRICT_DOCUMENT_METADATA_READ=true
STRICT_WORKFLOW_HISTORY_READ=true
STRICT_DECISION_AUDIT_READ=true
```

Kazda flage mozna wlaczac osobno. Wlaczenie jednej nie wymaga wlaczania pozostalych.

## Monitoring

Po wlaczeniu obserwuj logi:

- `strict_mode_enabled`,
- `strict_document_metadata_missing`,
- `strict_workflow_events_missing`,
- `strict_submission_decision_missing`,
- `strict_readiness_blocker`.

Logi zawieraja dane techniczne, takie jak `submission_id`, obszar, nazwa pliku i powod. Nie powinny zawierac PESEL, pelnych danych osobowych, adresow, `data_json` ani tresci maili.

## Rollback

Rollback polega na wylaczeniu odpowiedniej flagi:

```env
STRICT_DOCUMENT_METADATA_READ=false
STRICT_WORKFLOW_HISTORY_READ=false
STRICT_DECISION_AUDIT_READ=false
```

Nie wykonuj rollbacku danych. P4.5 nie usuwa danych ani kolumn legacy.

## Opcjonalna bramka deployowa

`REQUIRE_STRICT_READINESS_CHECK=false` jest domyslne. Przy `true` aplikacja loguje dodatkowe ostrzezenie `strict_readiness_blocker`, przypominajac, ze readiness musi zostac potwierdzony zewnetrznie przed wlaczeniem strict. Aplikacja nie skanuje calej bazy przy starcie.

## Stabilizacja po wlaczeniu strict

Przed stabilizacja zawsze sprawdz schemat P4:

```powershell
python scripts/check_p4_schema.py --report output/p4_schema_check.json
```

Jezeli raport wskazuje brak tabel albo kolumn, wykonaj migracje i sprawdz schemat ponownie:

```powershell
alembic current
alembic heads
alembic upgrade head
python scripts/check_p4_schema.py --report output/p4_schema_check_after_upgrade.json
```

Blad `schema_mismatch` oznacza, ze `ready_for_legacy_removal=false` i nalezy zostawic fallback.

Po wlaczeniu strict uruchom raport stabilizacji:

```powershell
python scripts/report_strict_mode_stabilization.py --area all --report output/strict_mode_stabilization.json
```

Obszar mozna uznac za stabilny, gdy:

- strict jest wlaczony dla tego obszaru,
- readiness jest zielony,
- `fallbacks_detected` wynosi `0`,
- `strict_events_detected` wynosi `0`,
- brak nowych logow `strict_document_metadata_missing`, `strict_workflow_events_missing` albo `strict_submission_decision_missing`,
- pelny pakiet testow przechodzi.

## Przejscie do legacy removal

Przejscie do migracji usuwajacej legacy wymaga:

- rekomendacji `ready_for_legacy_removal` w raporcie stabilizacji,
- spelnienia `LEGACY_REMOVAL_CHECKLIST.md`,
- zaakceptowania `LEGACY_REMOVAL_MIGRATION_PLAN.md`,
- decyzji z `LEGACY_APP_RETIREMENT_PLAN.md`,
- backupu bazy i storage,
- osobnego etapu migracji destrukcyjnej.

P4.6 nie usuwa pol legacy, nie usuwa `legacy_app.py` i nie wykonuje tej migracji.
