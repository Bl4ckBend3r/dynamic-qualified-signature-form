# P3 — obserwowalność, odtwarzanie awaryjne i wydajność

Ten runbook opisuje operacje utrzymaniowe dla PostgreSQL i MariaDB. Wszystkie próby restore i obciążenia wykonuj wyłącznie w środowisku odizolowanym. Migracje uruchamia job/pre-deploy, nigdy każdy worker aplikacji.

## Obserwowalność

### Konfiguracja

| Zmienna | Domyślna | Znaczenie |
|---|---:|---|
| `SERVICE_NAME` | `dynamic-qualified-signature-form` | nazwa usługi w logach |
| `LOG_FORMAT` | `text` | `text` albo JSON |
| `LOG_LEVEL` | `INFO` | poziom loggera procesu |
| `METRICS_ENABLED` | `true` | udostępnienie `/metrics` |
| `METRICS_TOKEN` | pusty | token Bearer lub `X-Metrics-Token`; bez tokenu tylko loopback |
| `TRUSTED_PROXY_HOPS` | `0` | liczba jawnie zaufanych proxy |

`services/observability.py` dodaje `request_id` i `correlation_id` do logów requestu oraz `X-Request-ID` do odpowiedzi. Przekazany przez klienta identyfikator jest honorowany tylko za zaufanym proxy i przy bezpiecznym formacie; inaczej powstaje UUID. Nieoczekiwane wyjątki są logowane ze stack trace po stronie serwera, a klient otrzymuje kontrolowane 500 z request ID.

Log JSON zawiera czas UTC, poziom, logger, zdarzenie, środowisko i nazwę usługi, a dla HTTP także metodę, endpoint, status i czas. Nie loguj payloadów, tokenów, PESEL-i, adresów e-mail, pełnych prywatnych nazw plików ani URL-i storage. Redaktor jest ostatnią barierą, nie zgodą na umieszczanie sekretów w komunikacie.

Zdarzenia domenowe obejmują utworzenie zgłoszenia, zapis wyboru i blokadę miejsca szkoleniowego, odrzucenie z powodu capacity, generację dokumentu, wynik weryfikacji podpisu, decyzję urzędnika, przejście workflow, błąd wysyłki maila, awarię storage i błąd readiness.

### Metryki i alerty

`/metrics` wystawia liczbę i czas requestów, czas/awarie operacji PDF, podpisu, storage i maila, odrzucenia capacity oraz błędy readiness. Etykiety mają ograniczoną kardynalność (`method`, endpoint Flask, klasa statusu, stabilna operacja/typ); nie zawierają ID zgłoszeń, tokenów ani ścieżek.

W produkcji ustaw długi `METRICS_TOKEN` i ogranicz endpoint również sieciowo. Docelowe alerty powinny co najmniej obejmować wzrost 5xx, p95/p99, błędy storage/maila/podpisu, brak świeżego backupu, nieudany restore test i readiness. Konkretne progi, kanały eskalacji i okna czasowe: **DO USTALENIA z właścicielem**.

`/live` odpowiada wyłącznie, czy proces HTTP żyje; nie dowodzi gotowości zależności. `/ready` sprawdza bazę i zgodność schematu i powinien sterować dopuszczeniem ruchu. Odpowiedź 503 z `/ready` oznacza pozostawienie instancji poza ruchem i diagnostykę po request ID/logach, nie restart w nieskończonej pętli.

## Backup

`scripts/maintenance/backup_restore.py` używa natywnego `pg_dump --format=custom`/`pg_restore` albo `mariadb-dump --single-transaction`/`mariadb`. Poświadczenia nie trafiają do argumentów komendy: PostgreSQL używa `PGPASSWORD`, MariaDB tymczasowego pliku klienta z ograniczonymi uprawnieniami.

```powershell
python -m scripts.maintenance.backup_restore backup --database-url $env:DATABASE_URL --output-dir C:\isolated\p3-backup --storage-source C:\isolated\storage
python -m scripts.maintenance.backup_restore verify --manifest C:\isolated\p3-backup\manifest-YYYYMMDDTHHMMSSZ.json
```

Manifest zapisuje czas UTC, silnik, rozmiar i SHA-256 dumpu, bieżącą rewizję i dynamicznie wyznaczony pojedynczy head Alembic, commit SHA oraz inventory/checksumy kontrolowanego storage. Nie zapisuje URL bazy ani haseł.

Opcja `--storage-source` służy testowej, kontrolowanej kopii lokalnego drzewa. Produkcyjny Nextcloud wymaga snapshotu/backupów infrastrukturalnych wykonanych spójnie z bazą; kopiowanie luźnych plików przez WebDAV nie jest snapshotem. Retencja dzienna/tygodniowa/miesięczna, szyfrowanie, klucze, kopia off-site, RPO i RTO: **DO USTALENIA z właścicielem i infrastrukturą**.

## Izolowany restore i test DR

Restore odmawia pracy, jeśli nie są spełnione wszystkie warunki:

- `P3_ALLOW_ISOLATED_RESTORE=true`;
- host jest lokalny lub nazwanym serwisem CI;
- nazwa bazy zawiera `test`, `restore` albo `_dr`;
- silnik odpowiada manifestowi;
- baza docelowa i katalog storage są puste;
- checksumy backupu są poprawne.

```powershell
$env:P3_ALLOW_ISOLATED_RESTORE='true'
python -m scripts.maintenance.backup_restore restore --manifest C:\isolated\p3-backup\manifest-YYYYMMDDTHHMMSSZ.json --database-url $env:RESTORE_DATABASE_URL --storage-destination C:\isolated\restored-storage
$env:DATABASE_URL=$env:RESTORE_DATABASE_URL
alembic upgrade head
python -m scripts.maintenance.verify_restore_integrity --database-url $env:RESTORE_DATABASE_URL --storage-root C:\isolated\restored-storage --expectations C:\isolated\fixture-expectations.json
```

Nigdy nie uruchamiaj tej procedury na produkcji. Po odtworzeniu sprawdź: SHA-256 każdego pliku, rewizję/current/head, wymagane tabele, dokładne liczniki fixture, osierocone relacje, dokument wygenerowany, dokument podpisany i załącznik, a także `/ready`. `.github/workflows/p3-restore.yml` wykonuje pełny test osobno dla PostgreSQL i MariaDB cyklicznie/manualnie, przerywa na błędzie i zachowuje artefakty przez 7 dni.

### Procedura incydentowa

1. Potwierdź incydent i wyznacz osobę decyzyjną; zabezpiecz logi i request ID.
2. Zatrzymaj dopływ zapisów: maintenance/read-only albo wyłączenie ruchu do workerów.
3. Wybierz wspólny punkt backupu DB i snapshotu Nextcloud zgodny z zaakceptowanym RPO.
4. Zweryfikuj manifest i SHA-256 przed restore.
5. Odtwórz DB odpowiednim narzędziem do nowej, pustej instancji tego samego silnika.
6. Odtwórz snapshot storage bez modyfikowania dokumentów i zweryfikuj inventory/checksumy.
7. Odczytaj `alembic current` oraz pojedynczy `alembic heads`.
8. Jeśli backup jest starszy od aplikacji, wykonaj `alembic upgrade head` raz, jako kontrolowany krok deploymentu. Worker nie migruje bazy.
9. Uruchom kontrolę integralności i wymagaj `/ready` = 200.
10. Wykonaj smoke odczytu formularza, zgłoszenia i trzech klas dokumentów bez wysyłania maili.
11. Po akceptacji właściciela stopniowo dopuść ruch i obserwuj błędy/latencję; zachowaj raport incydentu.

## Wydajność i konkurencja

Zainstaluj `requirements-dev.txt`. Dane są syntetyczne (`example.invalid`); dostępne rozmiary to 100, 1000 i 10000 zgłoszeń.

```powershell
python -m performance.seed_data --database-url $env:PERF_DATABASE_URL --submissions 1000 --output performance-context.json
locust -f performance\submissions_list.py --headless -u 10 -r 2 -t 60s --host http://127.0.0.1:8000 --csv p3-lists
locust -f performance\public_status.py --headless -u 10 -r 2 -t 60s --host http://127.0.0.1:8000 --csv p3-status
python -m performance.submissions_benchmark --sizes 100,1000 --repetitions 10 --output report.json
python -m performance.pdf_generation --concurrency 1,5,10,20 --repetitions 4 --output pdf-report.json
```

Listy obejmują wszystkie/moje/nieprzypisane/przeterminowane, filtrowanie i paginację; status publiczny sprawdza token poprawny, błędny i należący do innego zgłoszenia. Raportuj p50/p95/p99, throughput, błędy, liczbę zapytań SQL i szczyt pamięci PDF. Warianty PDF: simple, medium, tabela i logo; pliki wynikowe są usuwane.

`performance/check_regression.py` porównuje raport z zaakceptowanym baseline. Tolerancję p95, error rate i throughput zatwierdza właściciel przed włączeniem blokady CI; nie ustanawiaj arbitralnego SLA. `.github/workflows/p3-performance.yml` uruchamia krótki PR smoke oraz pełniejszy profil nightly/manualnie. Testów obciążeniowych nie uruchamiaj na produkcji.

Realna konkurencja miejsc używa `P3_POSTGRES_TEST_URL` i `P3_MARIADB_TEST_URL`. `tests/test_training_concurrency_integration.py` wymaga dokładnie jednego sukcesu dla 10 prób na jedno miejsce oraz 15 sukcesów dla 50 prób przy capacity 20 i pięciu istniejących blokadach, bez duplikatów i z końcowym stanem 20.

## Interpretacja i dalsze decyzje

- Wzrost liczby zapytań wraz z rozmiarem danych sugeruje N+1; stała liczba nie dowodzi jeszcze poprawnego planu zapytania.
- Indeks dodawaj dopiero po zebraniu planu/slow query na docelowym dialekcie i sprawdzeniu kosztu zapisu.
- Jedna próbka PDF jest smoke testem, nie baseline p95/p99.
- Alerty, retencja, RPO/RTO i tolerancja regresji pozostają decyzjami właściciela.
