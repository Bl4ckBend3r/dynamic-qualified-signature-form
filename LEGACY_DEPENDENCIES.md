# Legacy dependencies

Ten raport opisuje aktualne zaleznosci od `legacy_app.py` po etapie P3.0. Plik pozostaje w repozytorium jako warstwa zgodnosci dla testow i historycznych importow, ale runtime `create_app()` nie importuje juz `legacy_app.py`.

Po P3.1 `legacy_app.py` jest oznaczony w kodzie jako legacy compatibility module. Historyczne endpointy Flask sa opisane komentarzami `TODO(P3.x)` jako legacy-only i nie sa rejestrowane przez runtime `create_app()`.

Po P3.2 runtime nadal nie importuje `legacy_app.py`. Pobieranie dokumentow i legacy upload podpisanego PDF-a glownego formularza deleguja do `services/documents/document_download_service.py`, `services/documents/document_access_service.py` i `services/documents/document_signing_service.py`.

Po P3.3 runtime nadal nie importuje `legacy_app.py`. Flow deklaracji i umow dziala w `services/documents/declaration_flow_service.py` oraz `services/documents/agreement_flow_service.py`, a podpisane deklaracje i umowy przechodza przez `DocumentSigningService`.

Po P3.4 runtime nadal nie importuje `legacy_app.py`. `routes/documents.py` pozostaje pojedynczym cienkim adapterem HTTP, a ostatnie helpery aplikacyjne dla assetow i powiadomienia `AGREEMENT_SIGNED` zostaly przeniesione do serwisow dokumentowych.

Po P3.5 wybrano wariant A: `legacy_app.py` zostaje jawnie historycznym modulem zgodnosci. Runtime nadal go nie importuje, a martwy kod po delegujacych wrapperach zostal usuniety.

Po P3.6 nie zmieniano legacy. Runtime nadal nie importuje `legacy_app.py`, a zmiany dotycza wylacznie assetow frontendu dokumentow.

Po P4.0 nie zmieniano `legacy_app.py`. Runtime nadal nie importuje `legacy_app.py`; dodano tylko niedestrukcyjne struktury dual-write dla dokumentow, workflow i decyzji. Pola legacy w `FormSubmission` pozostaja aktywne jako zrodlo zgodnosci do czasu backfillu i przelaczenia odczytu.

Po P4.1 nadal nie zmieniano `legacy_app.py` i runtime nadal go nie importuje. Dodano samodzielny skrypt backfillu `scripts/backfill_p4_metadata.py`, ktory czyta pola legacy i uzupelnia nowe struktury, ale nie usuwa legacy ani nie przelacza odczytu aplikacji.

Po P4.2 nadal nie zmieniano `legacy_app.py` i runtime nadal go nie importuje. Fallbacki dotycza danych legacy w `FormSubmission` (`pdf_filename`, `declaration_*`, `agreement_*`, `training_agreements`, pola decyzji), a nie kodu `legacy_app.py`.

Po P4.3 nadal nie zmieniano `legacy_app.py` i runtime nadal go nie importuje. Nowy raport fallbackow obserwuje uzycie danych legacy w `FormSubmission`; nie przywraca zaleznosci od kodu `legacy_app.py`.

Po P4.4 nadal nie zmieniano `legacy_app.py` i runtime nadal go nie importuje. Dodano kontrolowane flagi strict dla odczytow danych legacy oraz readiness check przed ich wlaczeniem. Strict mode ogranicza odczyt z pol legacy `FormSubmission`, ale nie usuwa tych pol ani nie zmienia historycznego modulu zgodnosci.

Po P4.5 nadal nie zmieniano `legacy_app.py` i runtime nadal go nie importuje. Zmiany dotycza operacyjnego uzycia strict mode na danych legacy: logowania aktywnych flag, monitoringu zdarzen strict i planu rollout. Nie usunieto pol legacy ani historycznego modulu zgodnosci.

Po P4.6 nadal nie zmieniano `legacy_app.py` i runtime nadal go nie importuje. Dodano raport stabilizacji strict mode oraz plan decyzji `LEGACY_APP_RETIREMENT_PLAN.md`. Ten etap przygotowuje warunki usuniecia albo pozostawienia legacy app, ale nie przesadza, ze plik mozna usunac bez spelnienia checklisty.

Po P4.6.1 nadal nie zmieniano `legacy_app.py` i runtime nadal go nie importuje. Zmiany dotycza sprawdzenia schematu P4 i odpornosci raportow diagnostycznych na bledy SQL. Legacy removal pozostaje zablokowane przy `schema_mismatch`.

## Importy `legacy_app.py`

| Miejsce | Typ | Uzycie | Status |
| --- | --- | --- | --- |
| `tests/conftest.py` | test | Ustawia `legacy_app.app`, `legacy_app.storage` i `legacy_app.submission_repository` dla testow wrapperow zgodnosci. | Test only. |
| `tests/test_training_agreements.py` | test | Wrappery `extract_training_selection`, `build_training_agreement_number`, `generate_training_agreements_for_submission`, `ensure_declaration_generated`. | Testy regresji legacy. |
| `tests/test_training_agreement_service.py` | test | Delegacja `build_training_agreement_number`. | Wrapper zgodnosci. |
| `tests/test_document_split_services.py` | test | Delegacje wrapperow dokumentowych. | Wrappery zgodnosci P3.0. |
| `tests/test_runtime_legacy_imports.py` | test/audyt | Sprawdza brak rejestracji view funkcji z `legacy_app.py` w `create_app()`. | Audit only. |
| `tests/test_legacy_dependencies.py` | test/audyt | Sprawdza naglowek legacy i brak martwych blokow po wrapperach. | Audit only. |

`app.py`, `services/container.py`, `routes/documents.py` i moduly admina nie importuja `legacy_app.py`.

## Funkcje przeniesione do serwisow

| Element | Nowe miejsce | Status |
| --- | --- | --- |
| `get_training_selection_field` | `services/training_agreement_service.py` | Runtime uzywa serwisu; legacy deleguje. |
| `extract_training_selection` | `services/training_agreement_service.py` | Runtime uzywa serwisu; legacy deleguje. |
| `build_training_agreement_number` | `services/training_agreement_service.py` | Legacy deleguje. |
| `build_training_agreement_filename` | `services/documents/document_generation_service.py` | Legacy deleguje; format nazwy zachowany. |
| `build_agreement_block_updates` | `services/documents/document_generation_service.py` | Legacy deleguje; wartosci statusow bez zmian. |
| `build_declaration_form_definition` | `services/declaration_service.py` | Legacy deleguje. |
| `ensure_declaration_generated` | `services/declaration_service.py` | Orchestration przeniesione; legacy wrapper deleguje. |
| `generate_training_agreements_for_submission` | `services/documents/document_generation_service.py` | Orchestration przeniesione; legacy wrapper deleguje. |
| PDF render adapter | `services/documents/pdf_render_service.py` | Render przez `PdfRenderService`; legacy wrapper respektuje stare monkeypatche przez adapter. |
| Odczyt/zapis dokumentow | `services/documents/document_storage_service.py` | `SubmissionFile.storage_path` pozostaje zrodlem prawdy; fallback po nazwie jest tylko legacy i jest logowany. |
| Podpisane dokumenty | `services/documents/signed_document_service.py` | Nazwa `*-signed.pdf`, walidacja `%PDF` i aktualizacje podpisu sa wydzielone. |
| Pobieranie dokumentow | `services/documents/document_download_service.py` | Endpointy download w `routes/documents.py` zachowuja URL-e i deleguja walidacje nazwy, token oraz odczyt bajtow. |
| Dostep do dokumentow | `services/documents/document_access_service.py` | Sprawdzenie tokenu pobrania jest poza route. |
| Legacy upload podpisanego PDF formularza | `services/documents/document_signing_service.py` | Zapisuje PDF do dotychczasowej lokalizacji, aktualizuje `signed_pdf_filename` i `SubmissionFile`. |
| Flow deklaracji | `services/documents/declaration_flow_service.py` | Walidacja formularza deklaracji, dodatkowe pola po akceptacji i aktualizacje statusow legacy sa poza route. |
| Flow umow | `services/documents/agreement_flow_service.py` | Adapter `training_agreement` i generowanie kolekcji umow sa poza route. |
| Upload podpisanej deklaracji i umowy | `services/documents/document_signing_service.py` | Route deleguje do signing service, ktory wywoluje istniejace `DocumentService.upload_signed_document()`. |
| View model dokumentow | `services/documents/document_view_service.py` | Flagi statusu pochodza z `services/status_catalog.py`. |
| Readiness fallbackow legacy | `services/legacy_fallback_readiness_service.py` | Sprawdza, czy mozna wlaczyc strict dla dokumentow, workflow lub decyzji bez uzycia danych legacy. |
| Plan rollout strict mode | `scripts/check_legacy_fallback_readiness.py --recommend` | Generuje rekomendacje `enable_strict` albo `keep_fallback` bez zmiany konfiguracji i bez zapisow w bazie. |
| Stabilizacja strict mode | `services/strict_mode_stabilization_service.py` | Raportuje kandydatow do przyszlego legacy cleanup bez usuwania fallbackow. |

## Co nadal zostalo w `legacy_app.py`

| Element | Powod pozostawienia |
| --- | --- |
| Wrappery zgodnosci | Testy legacy nadal sprawdzaja historyczne importy i zachowanie bez zmiany publicznych URL-i. |
| Legacy endpointy w pliku | Nie sa rejestrowane przez `create_app()`, ale plik nie jest usuwany w P3.0. |
| Stare helpery publicznego formularza | Potrzebne tylko przy bezposrednim uruchomieniu `legacy_app.py`; nie sa zaleznoscia runtime fabryki aplikacji. |
| Bezposredni entrypoint | `if __name__ == "__main__"` pozostaje dla historycznego uruchomienia diagnostycznego. |

## Klasyfikacja zawartosci `legacy_app.py`

| Kategoria | Przyklady | Status |
| --- | --- | --- |
| Wrappery delegujace | `ensure_declaration_generated`, `generate_training_agreements_for_submission`, `build_training_agreement_filename`, `build_declaration_form_definition` | Zostaja dla zgodnosci i testow. |
| Historyczne endpointy Flask | `/`, `/form/<slug>`, `/submit/<slug>`, `/declaration/...`, `/agreements/...`, `/downloads/...`, `/do-podpisania` | Legacy-only, oznaczone `TODO(P3.x)`, nie rejestrowane przez `create_app()`. |
| Stare helpery publicznego formularza | rejestr formularzy, CSV, helpery PDF, helpery maili decyzji | Potrzebne tylko przy bezposrednim uruchamianiu legacy. |
| Martwy kod po delegacjach | stare fragmenty po `return` w wrapperach | Usuniete w P3.5 tam, gdzie byly jednoznacznie nieosiagalne i objete testami. |
| Elementy testowe | `LegacyPdfRenderAdapter`, globalne `app`, `storage`, `submission_repository` ustawiane w testach | Test only. |

## Aktualny stan runtime

Runtime import `legacy_app.py` zostal usuniety z fabryki aplikacji. `services/container.py` nie zawiera juz `install_legacy_helpers()` ani `import legacy_app`.

Po P3.2 `routes/documents.py` nadal jest pojedynczym modulem blueprintu, ale czesc endpointow dokumentowych deleguje do serwisow. Nie zmieniono publicznych nazw endpointow ani sciezek.

Po P3.3 `routes/documents.py` nadal jest pojedynczym modulem blueprintu. Logika aplikacyjna deklaracji, dodatkowych pol, generowania umow, uploadu podpisanych deklaracji/umow i view modelu dokumentow jest juz delegowana do serwisow.

Po P3.4 `routes/documents.py` jest formalnie zamkniety jako cienki modul HTTP. Import `from routes.documents import bp as documents_bp` pozostaje bez zmian.

## Blokery usuniecia `legacy_app.py`

1. Potwierdzenie, ze nikt nie uruchamia `legacy_app.py` bezposrednio jako aplikacji produkcyjnej.
2. Decyzja, czy historyczny entrypoint ma zostac zachowany do diagnostyki.
3. Przeniesienie albo usuniecie historycznych endpointow pozostalych w pliku `legacy_app.py`; runtime dokumentow nie wymaga juz legacy.
4. Przepiecie testow kompatybilnosci na nowe serwisy lub pozostawienie ich w osobnym pakiecie legacy.
5. Zielone raporty `scripts/check_legacy_fallback_readiness.py` dla obszarow, w ktorych strict ma zostac wlaczony.
6. Decyzja, czy `legacy_app.py` moze zostac usuniety w kolejnym etapie, czy ma pozostac jako jawny entrypoint historyczny.

## Kolejny rekomendowany krok

Uruchomic `scripts/report_strict_mode_stabilization.py`, przejsc `LEGACY_REMOVAL_CHECKLIST.md` i dopiero potem podjac osobna decyzje o migracji lub pozostawieniu legacy. Usuniecie `legacy_app.py` i pol legacy pozostaje osobnym etapem.
