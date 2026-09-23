# Plan porządkowania repozytorium

Porządki obejmują wyłącznie odtwarzalne artefakty lokalne. Nie wolno usuwać danych aplikacji, uploadów ani plików użytkownika na podstawie samego wzorca nazwy.

## Artefakty odtwarzalne

- `.coverage`, `coverage.xml` i `htmlcov/` — wyniki coverage;
- `.pytest_cache/` oraz katalogi `.pytest-tmp-*` — cache i pliki tymczasowe testów;
- `output/` — lokalne wyniki generowania, z zachowaniem kontrolowanych plików `.gitkeep` i zasad archiwum;
- `__pycache__/` i `*.pyc` — cache interpretera.

## Procedura

Najpierw sprawdzić `git status`, dokładnie rozwiązać ścieżki i zachować cudze zmiany. Po sprzątaniu ponownie sprawdzić status oraz uruchomić właściwe testy. Pliki śledzone, konfiguracja i dokumentacja nie są usuwane automatycznie.
