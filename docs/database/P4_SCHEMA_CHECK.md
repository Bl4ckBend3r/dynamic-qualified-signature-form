# Kontrola schematu P4

Kontrolę wykonuje się na kopii lub właściwym środowisku wdrożeniowym, nigdy przez samo `stamp`. Zalecana kolejność:

1. `alembic upgrade head` — utworzenie kompletnego fizycznego schematu.
2. `scripts/diagnostics/check_p4_schema.py` — kontrola tabel, kolumn i indeksów, w tym `submission_files.original_filename`.
3. `scripts/migration/backfill_p4_metadata.py` — idempotentne uzupełnienie canonical metadanych.
4. `scripts/diagnostics/check_legacy_fallback_readiness.py` — wykrycie braków i niejednoznaczności.
5. `scripts/diagnostics/report_strict_mode_stabilization.py` — raport obserwacji po włączeniu strict reads.

Jeżeli którykolwiek etap wykrywa brak, raport musi zachować `ready_for_legacy_removal=false`. Nie wolno włączać usuwania fallbacków wyłącznie na podstawie wersji z `alembic_version`.

## Transakcje i dialekty

Po błędzie PostgreSQL bieżąca transakcja może przejść w stan `InFailedSqlTransaction`; kontrola nie może kontynuować zapytań w tej samej uszkodzonej transakcji. DDL i inspekcja muszą pozostać zgodne z PostgreSQL oraz MariaDB, z użyciem SQLAlchemy/Alembic zamiast nieprzenośnych zapytań katalogowych.

## Kryterium gotowości

Wymagane są kompletne kolumny, zakończony backfill, brak niejednoznacznych dokumentów i zerowe odczyty fallback w okresie stabilizacji. Dopiero wtedy osobna zmiana może usuwać legacy.
