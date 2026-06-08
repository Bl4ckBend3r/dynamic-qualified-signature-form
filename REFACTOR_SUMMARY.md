# Refactor summary

## Zakres wykonany w tej iteracji

Wykonano etap P0 z raportu `AUDYT_KODU.md`:

- usunieto efekt uboczny wysylki maila z publicznego endpointu statusu,
- przeniesiono wysylke maila decyzji do zapisu decyzji urzednika w panelu admina,
- wzmocniono pobieranie dokumentow przez preferowanie `SubmissionFile.storage_path`,
- pozostawiono lookup po nazwie pliku tylko jako oznaczony w logach legacy fallback,
- dodano walidacje `SECRET_KEY` dla srodowiska produkcyjnego,
- dodano wspolny filtr sanitizacji HTML dla tresci konfigurowanych w formularzach,
- wzmocniono walidacje uploadow PDF i logo,
- dodano testy regresji dla zmian bezpieczenstwa.

## Przeniesione lub dodane moduly

| Modul | Cel |
|---|---|
| `services/upload_validation.py` | Centralna walidacja nazw plikow, PDF i logo. |
| `services/html_safety.py` | Polityka sanitizacji zaufanego HTML renderowanego w szablonach. |

## Usuniete elementy

Nie usuwano plikow ani pol bazy danych. Repozytorium ma aktywne zmiany robocze i artefakty wskazane w audycie powinny byc usuwane dopiero w osobnym, kontrolowanym etapie.

## Elementy oznaczone jako legacy

| Element | Status |
|---|---|
| Lookup PDF po nazwie pliku | Pozostal jako migracyjny fallback w `DocumentService.read_document_bytes_for_download()`, logowany ostrzezeniem. |
| `legacy_app.py` | Nadal istnieje i nadal jest importowany. Nie usuwano go w tej iteracji. |
| Pola legacy w `FormSubmission` | Nie ruszane bez migracji danych. |

## Zaleznosc od `legacy_app.py`

Nie zostala usunieta. Aktualne zaleznosci nadal obejmuja m.in. `routes/documents.py` i `services/container.py`. To pozostaje zadaniem P1/P2 po stabilizacji P0.

## Statusy i workflow

Statusy nie zostaly jeszcze w pelni scentralizowane. W tej iteracji zmieniono tylko zachowanie decyzji urzednika tak, aby mail decyzji byl wysylany przy faktycznej zmianie decyzji, a nie przy publicznym odczycie statusu.

## Maile

Maile nie zostaly jeszcze w pelni scentralizowane. Zmieniono krytyczne miejsce wysylki maila decyzji:

- przed: `GET /api/submissions/<id>/acceptance-status` mogl wyslac mail,
- teraz: mail decyzji jest wyzwalany przy zapisie decyzji w `routes/admin.py`.

## Dokumenty i `SubmissionFile.storage_path`

Pobieranie dokumentow uzywa teraz `DocumentService.read_document_bytes_for_download()`. Serwis:

1. szuka metadanych pliku przez `submission_repository.get_file_metadata()`,
2. jesli istnieje `storage_path`, czyta plik po tej sciezce,
3. jesli metadanych nie ma, uzywa legacy fallbacku po nazwie pliku.

## Testy dodane lub zmienione

| Test | Zakres |
|---|---|
| `tests/test_routes.py::test_acceptance_status_refresh_does_not_send_decision_email` | Publiczny status nie wysyla maila. |
| `tests/test_routes.py::test_download_pdf_rejects_token_from_other_submission` | Token jednego zgloszenia nie pobiera dokumentu innego. |
| `tests/test_routes.py::test_upload_signed_pdf_rejects_file_without_pdf_header` | Upload PDF bez naglowka `%PDF` jest odrzucany. |
| `tests/test_routes.py::test_form_page_sanitizes_configured_html` | HTML z konfiguracji formularza jest sanityzowany. |
| `tests/test_admin_panel.py::test_logo_upload_rejects_invalid_image_content` | Upload logo odrzuca nieobrazkowa zawartosc. |
| `tests/test_admin_panel.py::test_super_admin_can_upload_logo` | Fixture uploadu logo uzywa poprawnego minimalnego PNG. |
| `tests/test_app_factory.py::test_create_app_rejects_default_secret_key_in_production` | Produkcja nie startuje z domyslnym `SECRET_KEY`. |

## Wynik testow

Pelny zestaw testow przeszedl:

```text
139 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Etap P1 — statusy, workflow i legacy

Wykonano bez zmiany URL-i, pol bazy danych i bez usuwania `legacy_app.py`:

- dodano `services/status_catalog.py` jako katalog statusow, etykiet, flag, przejsc i mapowan legacy,
- `statuses.py` zostal adapterem zgodnosci do katalogu statusow,
- `services/workflow_service.py` korzysta z katalogu statusow dla etykiet i ma lekka metode `transition_submission()`,
- zapis decyzji urzednika w `routes/admin.py` przechodzi przez `WorkflowService.transition_submission()`,
- API statusu oddaje backendowe flagi `is_final`, `is_rejected`, `declaration_stage_completed`, `agreement_stage_completed`,
- `build_documents_to_sign_result()` dodaje backendowy view model statusu: `current_status`, label, flagi, `can_upload`, `can_download`, `visible_steps`, `visible_actions`,
- `templates/documents_to_sign.html` uzywa flag z API zamiast lokalnych list statusow odrzucenia i zakonczenia,
- dodano `LEGACY_DEPENDENCIES.md` i `ADMIN_SPLIT_PLAN.md`,
- dodano minimalny `services/mail_dispatch_service.py` jako punkt docelowy dla pozniejszej centralizacji maili.

Testy dodane lub zmienione w P1:

| Test | Zakres |
|---|---|
| `tests/test_status_catalog.py` | Normalizacja statusow, etykiety, flagi, przejscia i eksport dla frontendu. |
| `tests/test_workflow_service.py::test_transition_submission_validates_when_strict` | Centralna metoda przejsc statusu w workflow. |
| `tests/test_routes.py::test_acceptance_status_refresh_does_not_send_decision_email` | Publiczny status nadal nie wysyla maila i zwraca flagi katalogu statusow. |
| `tests/test_legacy_dependencies.py` | Raport legacy dokumentuje runtime importy. |

Wynik pelnego testu P1:

```text
145 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Etap P1.3 — dalsze ograniczenie legacy i przygotowanie P2

Wykonano bez zmiany publicznych URL-i, bez migracji bazy i bez usuwania `legacy_app.py`:

- przeniesiono `build_training_agreement_number()` do `services/training_agreement_service.py`,
- `legacy_app.py` deleguje numeracje umow szkoleniowych do nowego serwisu,
- nie przenoszono jeszcze `generate_training_agreements_for_submission()` ani `ensure_declaration_generated()`, bo lacza konfiguracje, storage i PDF renderer,
- dodano `services/admin_mail_context_service.py` dla `build_mail_context`, `render_mail_text`, `preview_mail_context` i `mail_template_type_score`,
- `routes/admin.py` pozostaje adapterem zgodnosci i nie zmienia endpointow,
- rozszerzono `MailDispatchService` o `render_subject()`, `render_body()`, `build_context_for_submission()` i bezpieczny dispatch z obsluga bledow,
- rozwinieto `services/document_naming_service.py` o `normalize_output_dir()` i `document_type_directory()`,
- nie wydzielono jeszcze CSS/JS z `templates/documents_to_sign.html`; utworzono szczegolowy `FRONTEND_SPLIT_PLAN.md`,
- utworzono `REPO_CLEANUP_PLAN.md` z analiza artefaktow bez usuwania.

Testy dodane lub zmienione w P1.3:

| Test | Zakres |
|---|---|
| `tests/test_training_agreement_service.py` | Numer umowy szkoleniowej w serwisie i delegacja wrappera legacy. |
| `tests/test_admin_mail_context_service.py` | Kontekst maila admina, preview i scoring typu szablonu. |
| `tests/test_mail_dispatch_service.py` | Render tematu/tresci, fallbacki i bledy dispatch. |
| `tests/test_document_naming_service.py` | Normalizacja katalogu wyjsciowego i katalog typu dokumentu. |
| `tests/test_frontend_split_plan.py` | Plan P2 dla wydzielenia CSS/JS dokumentow. |
| `tests/test_repo_cleanup_plan.py` | Plan porzadkow repozytorium obejmuje znane artefakty lokalne. |

Wynik pelnego testu P1.3:

```text
170 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Etap P2.1 — frontend, admin form service i plan migracji

Wykonano bez zmiany publicznych URL-i, bez migracji bazy i bez usuwania `legacy_app.py`:

- dodano bloki `extra_css` i `extra_js` w `templates/base.html`,
- dodano `static/documents_to_sign.css` z wydzielonymi stylami widoku dokumentow,
- dodano `static/documents_to_sign.js` z logika statusu, kafli, podmiany karty po pobraniu oraz drag-and-drop uploadu,
- nie usunieto jeszcze inline blokow z `templates/documents_to_sign.html`; zostalo to rozdzielone na osobny template-only commit opisany w `FRONTEND_SPLIT_PLAN.md`,
- dodano `services/admin_form_service.py` z helperami formularzy admina,
- `routes/admin.py` zachowuje kompatybilnosc i nie zmienia endpointow; pelne przepiecie na nowy serwis zostalo opisane w `ADMIN_SPLIT_PLAN.md`,
- dodano `MIGRATION_PLAN.md` dla modelu `FormSubmission` i pol legacy,
- zaktualizowano `ADMIN_SPLIT_PLAN.md` oraz `FRONTEND_SPLIT_PLAN.md`,
- nie usuwano artefaktow repozytorium i nie zmieniano bazy danych.

Testy dodane lub zmienione w P2.1:

| Test | Zakres |
|---|---|
| `tests/test_admin_form_service.py` | Parser HTML/JSON, wykrywanie pol, normalizacja stage, budowa definicji workflow i synchronizacja pol. |
| `tests/test_p2_1_frontend_assets.py` | Bloki `extra_css`/`extra_js`, istnienie plikow CSS/JS i kontrola braku lokalnych list statusow w nowym JS. |
| `tests/test_migration_plan.py` | Obecnosc wymaganych sekcji i pol legacy w `MIGRATION_PLAN.md`. |

Wynik testow P2.1:

```text
Nie uruchomiono w tej sesji — brak lokalnego checkoutu repozytorium przez narzedzie GitHub. Zmiany zostaly zapisane bezposrednio przez GitHub Contents API.
```

## Etap P1.2 — ograniczenie legacy i pierwsze wydzielenia

Wykonano bez zmiany publicznych URL-i, bez migracji bazy i bez usuwania `legacy_app.py`:

- przeniesiono helpery wyboru szkolen do `services/training_agreement_service.py`,
- `routes/documents.py` nie importuje juz `legacy_app.py`,
- `legacy_app.py` zostal jako warstwa zgodnosci dla starych importow testowych,
- `services/container.py` nadal instaluje legacy globale, ale ma komentarz techniczny z odniesieniem do `LEGACY_DEPENDENCIES.md`,
- utworzono `services/logo_service.py` i przeniesiono tam czyste helpery logo z admina,
- `routes/admin.py` zachowuje endpointy i nazwy helperow jako adaptery do `LogoService`,
- rozwinieto `services/mail_dispatch_service.py` o `render_template()`, `build_footer()`, `select_template()` i bezpieczne `dispatch()`,
- `build_footer_html()` w adminie deleguje do `MailDispatchService`,
- utworzono `services/document_naming_service.py` dla nazw dokumentow i sciezek PDF,
- `SubmissionService` i `DocumentService` korzystaja z nowego serwisu nazewnictwa,
- `services/file_metadata.py` korzysta z nowego resolvera sciezki, bez dodawania nowych fallbackow po nazwie.

Importy legacy nadal istnieja w:

- `services/container.py` dla migracyjnego `install_legacy_helpers()`,
- `tests/conftest.py`,
- `tests/test_training_agreements.py`.

Testy dodane w P1.2:

| Test | Zakres |
|---|---|
| `tests/test_training_agreement_service.py` | Wybor pola szkolen, ekstrakcja szkolen, wymaganie wyboru i limit kwoty. |
| `tests/test_runtime_legacy_imports.py` | `routes/documents.py` nie importuje `legacy_app.py`. |
| `tests/test_logo_service.py` | Bezpieczna nazwa assetu i reguly wyboru logo. |
| `tests/test_mail_dispatch_service.py` | Render szablonu, stopka, bezpieczny dispatch bez nadawcy. |
| `tests/test_document_naming_service.py` | Nazwy deklaracji, umowy, podpisu i sciezki PDF bez path traversal. |

Wynik pelnego testu P1.2:

```text
160 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Pozostawione na kolejne etapy

| Priorytet | Zadanie |
|---|---|
| P1 | Usuniecie pozostalych zaleznosci nowych modulow od `legacy_app.py`. |
| P1 | Podzial `routes/admin.py` na mniejsze blueprinty i serwisy. |
| P1 | Centralizacja maili w jednym mechanizmie dispatch razem z logowaniem `EmailLog`. |
| P1 | Podzial `services/document_service.py` na mniejsze serwisy dokumentowe. |
| P2 | Przepiecie `routes/admin.py` na `services/admin_form_service.py`. |
| P2 | Usuniecie inline CSS/JS z `templates/documents_to_sign.html` po zaladowaniu nowych assetow przez bloki `extra_css` i `extra_js`. |
| P2 | Wykonanie migracji `FormSubmission` wedlug `MIGRATION_PLAN.md`. |
| P3 | Porzadki repozytorium: `.coverage`, `.pytest_cache/`, `tmp/logos/`, `output/` po potwierdzeniu, ze nie sa fixture. |
