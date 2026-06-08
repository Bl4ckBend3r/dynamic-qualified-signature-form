# Repo cleanup plan

Analiza bez usuwania plikow. Rekomendacje wymagaja osobnego potwierdzenia przed kasowaniem.

| Artefakt | Status Git | Uzycie w testach/fixture | Rekomendacja | Ryzyko |
| --- | --- | --- | --- | --- |
| `.coverage` | Lokalny plik w katalogu glownym | Wynik uruchomienia coverage/pytest, nie fixture | Usunac po potwierdzeniu albo dodac do `.gitignore` | Niskie |
| `.pytest_cache/` | Lokalny katalog cache | Cache pytest, nie fixture | Usunac po potwierdzeniu albo dodac do `.gitignore` | Niskie |
| `tmp/audit_log.jsonl` | Generowany podczas testow | Efekt uboczny `AuditLogService` w testach | Nie commitowac; rozwazyc izolacje TEMP_DIR w testach | Niskie |
| `tmp/logos/` | Lokalny katalog uploadow logo | Moze powstawac w testach admina | Nie commitowac; zostawic lokalnie lub czyscic po testach | Srednie, jesli zawiera reczne pliki testowe |
| `output/` | Lokalny katalog wynikow | Moze zawierac wygenerowane PDF/CSV i dane testowe | Nie usuwac automatycznie; najpierw sprawdzic zawartosc | Srednie |
| `__pycache__/` | Lokalny cache Pythona | Nie fixture | Usunac po potwierdzeniu albo dodac do `.gitignore` | Niskie |

## Kolejny krok

Przed sprzataniem uruchomic `git status --ignored` i `rg` dla nazw plikow z `output/` oraz `tmp/logos/`, aby potwierdzic, ze nie sa fixture ani recznym materialem testowym.
