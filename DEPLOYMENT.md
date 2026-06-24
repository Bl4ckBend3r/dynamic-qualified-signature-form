# Wdrozenie produkcyjne

## Wymagania

- Docker
- Docker Compose
- Dostep do Nextcloud
- Dane SMTP, jezeli system ma wysylac e-maile
- PostgreSQL uruchamiany z Compose albo zewnetrzny PostgreSQL

## Konfiguracja

Skopiuj plik srodowiskowy:

```bash
cp .env.example .env
```

Wypelnij wymagane zmienne:

```env
FLASK_ENV=production
FLASK_DEBUG=false
SECRET_KEY=uzupelnij_silnym_losowym_kluczem
POSTGRES_DB=formularze
POSTGRES_USER=formularze
POSTGRES_PASSWORD=uzupelnij_haslo
DATABASE_URL=postgresql+psycopg://formularze:uzupelnij_haslo@postgres:5432/formularze
NEXTCLOUD_BASE_URL=https://nextcloud.example.pl
NEXTCLOUD_USERNAME=uzupelnij
NEXTCLOUD_APP_PASSWORD=uzupelnij
```

## Uruchomienie

```bash
docker compose up -d --build
```

## Sprawdzenie dzialania

```bash
curl http://localhost:8000/health
```

Oczekiwany wynik:

```json
{
  "app": "ok",
  "nextcloud": "ok"
}
```

## Logi

```bash
docker compose logs -f app
```

## Restart

```bash
docker compose restart app
```

## Backup

Backup powinien obejmowac:

- wolumen `postgres_data`,
- katalogi Nextcloud z formularzami i wynikami,
- plik `.env`,
- szablony HTML i JSON przechowywane w Nextcloud.

## Aktualizacja aplikacji

```bash
git pull
docker compose up -d --build
```
