# Skrypty utrzymaniowe

- `admin/` — zadania administracyjne i kompatybilne entrypointy operacyjne.
- `archive/` — zachowane stare lub niepewne skrypty.
- `deployment/` — helpery uruchomieniowe i wdrożeniowe.
- `diagnostics/` — kontrole schematu i raporty gotowości.
- `maintenance/` — bezpieczne narzędzia utrzymaniowe dla plików projektu.
- `migration/` — migracje i naprawy danych; historia Alembic pozostaje w
  `migrations/versions/`.
- `qa/` — lokalne helpery kontroli jakości.

Skrypty uruchamiaj z katalogu głównego repozytorium, na przykład:

```powershell
python scripts/diagnostics/check_p4_schema.py
```
