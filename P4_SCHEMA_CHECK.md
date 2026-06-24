# P4 schema check

P4.6.1 naprawia diagnostyke po raporcie stabilizacji, ktory wykryl brak kolumny `submission_files.original_filename` w jednej z baz. Ta kolumna jest w modelu `SubmissionFile` i w migracji `20260610_0009_p4_dual_write_audit_structures.py`, wiec najpierw trzeba potwierdzic stan schematu i wykonanie migracji.

## Co sie stalo

Raport stabilizacji strict mode pokazal blad:

```text
psycopg.errors.UndefinedColumn: kolumna submission_files.original_filename nie istnieje
```

Kolejne bledy `InFailedSqlTransaction` byly kaskadowe: PostgreSQL przerwal transakcje po pierwszym bledzie SQL, a nastepne zapytania byly odrzucane do czasu rollbacku.

## Kolejnosc operacyjna

1. Sprawdz schemat:

```powershell
python scripts/check_p4_schema.py --report output/p4_schema_check.json
```

2. Jezeli brakuje tabel albo kolumn, sprawdz migracje:

```powershell
alembic current
alembic heads
alembic upgrade head
```

3. Sprawdz schemat ponownie:

```powershell
python scripts/check_p4_schema.py --report output/p4_schema_check_after_upgrade.json
```

4. Uruchom backfill w dry-run:

```powershell
python scripts/backfill_p4_metadata.py --dry-run --report output/backfill_p4_report.json
```

5. Po akceptacji uruchom backfill apply:

```powershell
python scripts/backfill_p4_metadata.py --apply --report output/backfill_p4_apply_report.json
```

6. Uruchom readiness:

```powershell
python scripts/check_legacy_fallback_readiness.py --area all --recommend --report output/strict_rollout_plan.json
```

7. Uruchom stabilizacje:

```powershell
python scripts/report_strict_mode_stabilization.py --area all --report output/strict_mode_stabilization.json
```

8. Dopiero po zielonym schema check, backfill, readiness i stabilization podejmuj decyzje o strict mode albo legacy removal.

## Interpretacja raportu

`scripts/check_p4_schema.py` zwraca:

- `0` - schemat zgodny,
- `1` - brakuje tabel albo kolumn,
- `2` - blad techniczny.

Przy bledzie schematu raport stabilizacji musi miec:

- `ready_for_legacy_removal=false`,
- `requires_schema_upgrade=true`,
- `recommended_action=keep_fallback` dla obszarow z bledem schematu.

## Zachowanie aplikacji przy niewykonanej migracji

Pola legacy pozostaja zrodlem zgodnosci. Zapis decyzji urzednika powinien zapisac aktualne pola `FormSubmission` nawet wtedy, gdy dual-write do `SubmissionDecision` nie jest mozliwy z powodu braku tabeli. Taki przypadek musi byc zalogowany jako `schema_mismatch`, a srodowisko nadal wymaga `alembic upgrade head`.

Odczyty metadanych `SubmissionFile` w panelu admina powinny w takim stanie zwracac puste listy metadanych i logowac `schema_mismatch`, zamiast przerywac widok bledem 500. To jest degradacja diagnostyczna, nie potwierdzenie gotowosci schematu.

## Zakazy P4.6.1

- Nie usuwaj pol legacy.
- Nie usuwaj `legacy_app.py`.
- Nie wykonuj destrukcyjnej migracji.
- Nie wlaczaj strict mode przed zielonym schema check.
- Nie przechodz do legacy removal przed schema check, migracjami, backfillem, readiness i stabilization.
