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

## Etap P2.2 — przepisanie admin form service

Wykonano bez zmiany publicznych URL-i, bez zmiany nazw endpointow, bez migracji bazy i bez usuwania `legacy_app.py`:

- `routes/admin.py` importuje helpery formularzy z `services/admin_form_service.py`,
- usunieto lokalne duplikaty funkcji: `parse_uploaded_form_definition`, `normalize_admin_form_definition`, `validate_admin_form_config`, `build_form_definition_from_admin_form`, `parse_workflow_json`, `build_definition_from_html`, `build_definition_from_docx`, `html_attr`, `humanize_field_name`, `sync_form_fields`, `detect_form_fields`, `normalize_field_stage`, `form_has_additional_fields`,
- walidacja definicji formularza i konfiguracji admina jest wykonywana przez `validate_admin_form_config()` w serwisie,
- nie tworzono `admin_template_import_service.py`; importery HTML/DOCX pozostaja w `admin_form_service.py`,
- usunieto z `routes/admin.py` importy potrzebne tylko przez stare lokalne helpery.

Testy dodane lub zmienione w P2.2:

| Test | Zakres |
|---|---|
| `tests/test_admin_form_service.py` | DOCX parser, normalizacja i walidacja konfiguracji, niepoprawna definicja, `parse_workflow_json`, `form_has_additional_fields`. |
| `tests/test_admin_panel.py` | Regresja uploadu formularzy, edycji workflow i list admina pozostala zielona. |

Wynik testow P2.2:

```text
187 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Etap P2.8 - centralizacja maili i wydzielenie mail admina

Wykonano bez zmiany publicznych URL-i, bez migracji bazy i bez usuwania `legacy_app.py`:

- `services/mail_dispatch_service.py` jest glowna fasada renderowania, wysylki i logowania maili admina oraz maili decyzji.
- Dodano `MailDispatchResult`, centralne `dispatch_raw()`, `dispatch_to_submission()`, `dispatch_decision_email()` i `log_email()`.
- `EmailLog` jest zapisywany centralnie przez `MailDispatchService` przy sukcesie, bledzie i bezpiecznym pominieciu, jezeli model jest dostepny; w lekkim srodowisku bez modelu serwis uzywa bezpiecznego fallbacku bez migracji.
- `NotificationService` nadal istnieje jako adapter niskiego poziomu dla SMTP i legacy/eventowych powiadomien formularzy.
- Mail decyzji urzednika w `routes/admin/submissions.py` przechodzi przez `MailDispatchService` i jest wysylany tylko przy faktycznej zmianie decyzji.
- Reczny mail do jednego zgloszenia i bulk mail do zaznaczonych zgloszen przechodza przez `MailDispatchService`.
- Utworzono `routes/admin/mail.py` na wspolnym blueprintcie `bp`.
- Do `routes/admin/mail.py` przeniesiono endpointy: `submission_mail`, `submissions_mail_selected`, `mail_templates_list`, `mail_templates_index`, `mail_template_edit`, `mail_template_import_html`, `mail_template_import_zip`, `mail_footers_list`, `mail_footer_edit`.
- URL-e i nazwy endpointow mailowych pozostaly bez zmian.
- `routes/admin/__init__.py` nadal eksportuje `bp`, `login_required`, `role_required`, `get_current_user` oraz cienkie adaptery zgodnosci `select_mail_template()` i `mail_template_type_score()`.

Sciezki maili:

| Sciezka | Status po P2.8 |
|---|---|
| Decyzja urzednika | Przepieta na `MailDispatchService.dispatch_decision_email()`. |
| Reczny mail admina do jednego zgloszenia | Przepiety na `MailDispatchService.dispatch_to_submission()`. |
| Bulk mail do zaznaczonych zgloszen | Przepiety na `MailDispatchService.dispatch_to_submission()` przez cienki adapter w `routes/admin/mail.py`. |
| Szablony i stopki maili admina | Endpointy przeniesione do `routes/admin/mail.py`, render przechodzi przez serwis. |
| `NotificationService.notify_event()` i `notify_event_once()` | Pozostaja jako adaptery legacy/workflow dla konfiguracji formularzy. |

Testy dodane lub zmienione w P2.8:

| Test | Zakres |
|---|---|
| `tests/test_mail_dispatch_service.py` | Kontekst zgloszenia, centralne logowanie sukcesu, fallback braku odbiorcy. |
| `tests/test_admin_panel.py::test_officer_decision_mail_uses_mail_dispatch_service_once` | Decyzja urzednika wysyla mail przez `MailDispatchService` tylko raz dla faktycznej zmiany. |
| `tests/test_admin_panel.py::test_admin_mail_module_keeps_endpoint_names` | `routes.admin.mail` jest importowalne, `bp` pozostaje wspolny, a stare endpointy mailowe dzialaja. |

Wynik testow P2.8 dla obszaru mail/admin:

```text
56 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_mail_dispatch_service.py tests\test_admin_panel.py -q
```

Wynik pelnego testu P2.8 w `.venv`:

```text
206 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Etap P2.3 — pierwszy podzial admina

Wykonano pierwszy bezpieczny krok bez zmiany publicznych URL-i, nazw endpointow, modelu bazy, migracji i bez usuwania `legacy_app.py`.

- nie utworzono `routes/admin/logos.py`, bo obecny routing ma `routes/admin.py` jako plik, a aplikacja importuje `from routes.admin import bp as admin_bp`; zmiana na pakiet wymaga osobnego kroku importowego,
- modul admina pozostal adapterem dla endpointow `/admin/logos`, `/admin/logos/<id>/toggle`, `/admin/logos/<id>/edit` i `/admin/logos/<id>/asset`,
- przeniesiono pozostala logike logo do `services/logo_service.py`: liste widocznych logo dla admina, tworzenie logo z uploadu, aktualizacje metadanych oraz rozstrzyganie sciezki assetu z kontrola roli,
- endpointy logo dalej korzystaja z tych samych URL-i i nazw `admin.logos_list`, `admin.logo_toggle`, `admin.logo_edit`, `admin.logo_asset`,
- poprawiono odczyt stanu aktywnosci po toggle tak, aby komunikat flash nie korzystal z encji po zamknieciu sesji.

Testy dodane lub zmienione w P2.3:

| Test | Zakres |
|---|---|
| `tests/test_logo_service.py` | Tworzenie logo z uploadu, widocznosc logo dla superadmina i managera, sciezka assetu oraz aktualizacja metadanych. |
| `tests/test_admin_panel.py` | Toggle logo, pobranie assetu pod starym URL-em oraz blokada operacji toggle dla zwyklego admina. |

Wynik testow P2.3:

```text
191 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Etap P2.4 — przygotowanie pakietu admina

Wykonano maly krok importowy bez zmiany publicznych URL-i, nazw endpointow, modelu bazy, migracji i bez usuwania `legacy_app.py`.

- przeniesiono `routes/admin.py` do `routes/admin/__init__.py`,
- zachowano import `from routes.admin import bp as admin_bp` uzywany w `app.py`,
- nie przenoszono jeszcze endpointow do `routes/admin/logos.py`, `routes/admin/forms.py` ani innych modulow potomnych,
- obecny blueprint admina nadal dziala jako pojedynczy modul, ale struktura plikow pozwala juz bezpiecznie dodawac kolejne moduly w pakiecie `routes/admin/`,
- dodano test regresji potwierdzajacy, ze `routes.admin` importuje blueprint z pakietu.

Test dodany w P2.4:

| Test | Zakres |
|---|---|
| `tests/test_admin_package_structure.py` | Import `routes.admin`, nazwa blueprintu i brak starego pliku `routes/admin.py`. |

Wynik testow P2.4:

```text
192 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Etap P2.5 — wydzielenie endpointow logo

Wykonano bez zmiany publicznych URL-i, nazw endpointow, modelu bazy, sposobu zapisu plikow logo, migracji i bez usuwania `legacy_app.py`.

- utworzono `routes/admin/logos.py`,
- przeniesiono endpointy: `logos_list`, `logo_toggle`, `logo_edit`, `logo_asset`,
- URL-e pozostaly bez zmian: `/admin/logos`, `/admin/logos/<id>/toggle`, `/admin/logos/<id>/edit`, `/admin/logos/<id>/asset`,
- nazwy endpointow pozostaly bez zmian: `admin.logos_list`, `admin.logo_toggle`, `admin.logo_edit`, `admin.logo_asset`,
- `routes/admin/__init__.py` nadal eksportuje wspolny blueprint `bp`,
- `routes/admin/__init__.py` importuje `routes.admin.logos` na koncu pliku, aby zarejestrowac trasy na tym samym blueprintcie,
- logika biznesowa logo pozostala w `services/logo_service.py`, a routing obsluguje request, flash, redirect, render i wywolania serwisu.

Testy dodane lub zmienione w P2.5:

| Test | Zakres |
|---|---|
| `tests/test_admin_package_structure.py` | Import `routes.admin.logos`, rejestracja endpointow logo na blueprintcie i zgodnosc `url_for()` dla starych nazw endpointow. |
| `tests/test_admin_panel.py` | Regresja listy logo, uploadu, edycji, toggle, assetu i blokady operacji superadminowych pozostala zielona. |

Wynik testow P2.5:

```text
193 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Etap P2.6 — wydzielenie endpointow formularzy

Wykonano bez zmiany publicznych URL-i, nazw endpointow, modelu bazy, migracji i bez usuwania `legacy_app.py`. Modul maili, zgloszen i uzytkownikow nie byl przenoszony w tej iteracji.

- utworzono `routes/admin/forms.py`,
- przeniesiono endpointy: `forms_list`, `form_delete`, `forms_upload`, `form_edit`, `form_toggle`, `form_fields`,
- URL-e pozostaly bez zmian: `/admin/forms`, `/admin/forms/upload`, `/admin/forms/<id>/edit`, `/admin/forms/<id>/delete`, `/admin/forms/<id>/toggle`, `/admin/forms/<id>/fields`,
- nazwy endpointow pozostaly bez zmian: `admin.forms_list`, `admin.forms_upload`, `admin.form_edit`, `admin.form_delete`, `admin.form_toggle`, `admin.form_fields`,
- `routes/admin/__init__.py` nadal eksportuje wspolny blueprint `bp`,
- `routes/admin/__init__.py` importuje `routes.admin.forms` na koncu pliku, obok `routes.admin.logos`,
- z `routes/admin/__init__.py` usunieto endpointy formularzy oraz importy uzywane tylko przez nie: budowe definicji z formularza admina, upload/validacje/synchronizacje definicji, `TRIGGER_DESCRIPTIONS` oraz stale typow i etapow pol,
- wspolne helpery dostepu, pola aktywne, `normalize_slug`, `parse_field_options`, `field_options_text` i adaptery logo pozostaly w pakiecie admina, bo sa nadal uzywane przez formularze oraz inne obszary panelu.

Testy dodane lub zmienione w P2.6:

| Test | Zakres |
|---|---|
| `tests/test_admin_package_structure.py` | Import `routes.admin.forms`, rejestracja endpointow formularzy na blueprintcie i zgodnosc `url_for()` dla starych nazw endpointow. |
| `tests/test_admin_panel.py` | Regresja listy formularzy, uploadu, edycji workflow, pol, toggle i usuwania formularzy pozostala zielona. |
| `tests/test_admin_form_service.py` | Serwis formularzy nadal pokrywa parsery, walidacje, normalizacje i synchronizacje. |

Wynik testow P2.6:

```text
194 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Etap P2.7 — wydzielenie users, submissions, dashboard i auth

Wykonano bez zmiany publicznych URL-i, nazw endpointow, modelu bazy, migracji i bez usuwania `legacy_app.py`. Modul maili pozostal wtedy w `routes/admin/__init__.py`; stan ten zostal zaktualizowany w P2.8 po centralizacji `MailDispatchService`.

- utworzono `routes/admin/users.py` i przeniesiono `users_list`, `user_toggle_block`, `user_edit`,
- utworzono `routes/admin/submissions.py` i przeniesiono `submissions_all`, `submissions_list`, `submission_detail`, `submission_decision_update`,
- utworzono `routes/admin/dashboard.py` i przeniesiono `dashboard`,
- utworzono `routes/admin/auth.py` i przeniesiono `admin_index`, `login`, `logout`, `load_current_user`, `login_required`, `role_required`, `get_current_user`,
- `routes/admin/__init__.py` nadal eksportuje wspolny blueprint `bp` oraz re-eksportuje `login_required`, `role_required`, `get_current_user`,
- `routes/admin/__init__.py` importuje moduly potomne: `auth`, `dashboard`, `forms`, `logos`, `submissions`, `users`,
- utworzono `services/admin_submission_service.py` dla `admin_status_label`, `submission_value`, `build_filter_fields`, `filter_submissions`, `sort_submissions`,
- etykiety statusow admina w serwisie zgłoszen przechodza przez `workflow_status_label()` oraz `services/status_catalog.py`,
- wspolne helpery dostepu (`accessible_form_ids`, `accessible_form_slugs`, `list_accessible_forms`, `ensure_form_access`) zostaly tymczasowo w `routes/admin/__init__.py`, bo korzystaja z nich formularze, zgłoszenia, dashboard i mail.

Testy dodane lub zmienione w P2.7:

| Test | Zakres |
|---|---|
| `tests/test_admin_package_structure.py` | Import nowych modulow admina i rejestracja endpointow auth, dashboardu, users oraz submissions na wspolnym blueprintcie. |
| `tests/test_admin_submission_service.py` | Status label przez workflow/status catalog, fallback nieznanego statusu, filtrowanie i sortowanie po polach legacy oraz `data_json`. |
| `tests/test_admin_panel.py` | Regresja logowania, dashboardu, list formularzy, zgłoszen, decyzji, users, logo i maili admina pozostala zielona. |

Wynik testow P2.7:

```text
201 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Etap P2.9 - ograniczenie legacy i przygotowanie podzialu dokumentow

Wykonano bez migracji bazy, bez usuwania pol legacy, bez usuwania `legacy_app.py`, bez zmiany publicznych URL-i i bez zmiany lokalizacji dokumentow:

- utworzono pakiet `services/documents/`,
- dodano `PdfRenderService` jako adapter dotychczasowego renderowania PDF,
- dodano `DocumentStorageService`, ktory preferuje `SubmissionFile.storage_path`, blokuje path traversal i loguje legacy fallback po nazwie,
- dodano `SignedDocumentService` dla walidacji `%PDF`, nazwy `*-signed.pdf` i aktualizacji metadanych podpisu,
- dodano `DocumentViewService`, ktory buduje view model dokumentow z flagami z `status_catalog`,
- dodano `services/declaration_service.py` dla czystych helperow deklaracji,
- `DocumentService` korzysta z nowych serwisow, ale nadal zachowuje eksporty zgodnosci `generate_document_pdf_bytes()`, `build_signed_filename()`, `remove_inline_logo_markup()` i `prepare_document_template_html()`,
- `legacy_app.py` deleguje `build_training_agreement_filename()`, `build_agreement_block_updates()` i `build_declaration_form_definition()` do nowych serwisow,
- na koniec P2.9 `ensure_declaration_generated()` i `generate_training_agreements_for_submission()` zostaly jeszcze w legacy jako orchestration laczacy storage, PDF, CSV i workflow; stan zostal zmieniony w P3.0.

Na koniec P2.9 pozostawal runtime import `legacy_app.py`: `services/container.py` przez `install_legacy_helpers()`. Stan zostal zmieniony w P3.0.

`SubmissionFile.storage_path` nadal jest zrodlem prawdy dla odczytu dokumentow. Fallback po nazwie pliku pozostal tylko jako legacy fallback i jest logowany.

Testy dodane w P2.9:

| Test | Zakres |
|---|---|
| `tests/test_document_split_services.py` | `storage_path`, legacy fallback, path traversal, walidacja podpisanego PDF, nazwa podpisu, PDF adapter, view model i delegacja wrappera legacy. |

Wynik testow P2.9:

```text
215 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Etap P3.0 - przepiecie orchestration dokumentow z legacy

Wykonano bez migracji bazy, bez usuwania pol legacy, bez usuwania `legacy_app.py`, bez zmiany publicznych URL-i i bez zmiany lokalizacji dokumentow:

- przeniesiono `ensure_declaration_generated()` do `services/declaration_service.py`,
- przeniesiono `generate_training_agreements_for_submission()` do `services/documents/document_generation_service.py`,
- nowe orchestration deklaracji renderuje PDF przez `PdfRenderService`, zapisuje przez `DocumentStorageService`, aktualizuje pola `declaration_*` i opcjonalnie zapisuje `SubmissionFile`,
- nowe orchestration umow szkoleniowych zachowuje format numerow, format nazw plikow, obsluge wielu szkolen, pole `training_agreements` i pola `agreement_*`,
- `legacy_app.py` zostal wrapperem zgodnosci dla przeniesionych funkcji; wrapper PDF respektuje stare monkeypatche testowe,
- `create_app()` nie wywoluje juz `install_legacy_helpers()`,
- `services/container.py` nie importuje juz `legacy_app.py` i nie zawiera `install_legacy_helpers()`,
- runtime import `legacy_app.py` zostal usuniety; importy pozostaja tylko w testach zgodnosci legacy.

`SubmissionFile.storage_path` nadal jest zrodlem prawdy dla odczytu dokumentow. Nie dodano nowych fallbackow po nazwie.

Testy dodane lub zmienione w P3.0:

| Test | Zakres |
|---|---|
| `tests/test_document_split_services.py` | Orchestration deklaracji, orchestration wielu umow, zapis przez `DocumentStorageService`, aktualizacja pol legacy i delegacja wrapperow legacy. |
| `tests/test_runtime_legacy_imports.py` | `app.py` i `services/container.py` nie instaluja legacy helperow ani nie importuja `legacy_app.py`. |
| `tests/conftest.py` | Testy legacy ustawiaja wrapperom testowy `app`, `storage` i `submission_repository` poza runtime aplikacji. |

Wynik testow P3.0:

```text
219 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Etap P3.1 - zamkniecie legacy jako modulu zgodnosci

Wykonano bez migracji bazy, bez usuwania pol legacy, bez usuwania `legacy_app.py`, bez zmiany publicznych URL-i i bez zmiany lokalizacji dokumentow:

- dodano na gorze `legacy_app.py` techniczny naglowek compatibility module,
- oznaczono historyczne endpointy Flask w `legacy_app.py` komentarzami `TODO(P3.x)` jako legacy-only, nieuzywane przez runtime `create_app()`,
- runtime nadal nie importuje `legacy_app.py`,
- wrappery legacy nadal deleguja do nowych serwisow i pozostaja dla historycznych importow oraz testow regresji,
- przeniesiono czysty helper numeracji dokumentu do `services/documents/document_generation_service.py`,
- `DocumentService.build_document_number()` deleguje do generation service,
- usunieto z `DocumentService` nieuzywane helpery view-modelu, ktore zastapil `DocumentViewService`.

`SubmissionFile.storage_path` nadal jest zrodlem prawdy dla odczytu dokumentow. Legacy fallback po nazwie nie zostal rozszerzony i nadal jest logowany tylko dla starszych rekordow.

Testy dodane lub zmienione w P3.1:

| Test | Zakres |
|---|---|
| `tests/test_runtime_legacy_imports.py` | `app.py`, `services/container.py`, `routes/documents.py` i `routes/admin/*.py` nie importuja `legacy_app.py`; `create_app()` nie instaluje legacy helperow. |
| `tests/test_document_split_services.py` | Delegacja `build_declaration_form_definition()`, respektowanie monkeypatchy PDF przez legacy adapter i numer dokumentu w generation service. |

Wynik testow P3.1:

```text
223 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Etap P3.2 - wydzielenie endpointow dokumentow

Wykonano bez migracji bazy, bez usuwania pol legacy, bez usuwania `legacy_app.py`, bez zmiany publicznych URL-i i bez zmiany lokalizacji dokumentow:

- `routes/documents.py` nie zostal jeszcze zamieniony na pakiet `routes/documents/*`, bo plik nadal zawiera aktywne przeplywy podpisow, deklaracji i umow; podzial route bedzie bezpieczniejszy po dalszym odchudzeniu helperow,
- zachowano import zgodnosci `from routes.documents import bp as documents_bp`,
- dodano `services/documents/document_access_service.py` jako cienka warstwe sprawdzania dostepu do pobran,
- dodano `services/documents/document_download_service.py` do walidacji nazw PDF, sprawdzania tokenu i przygotowania odpowiedzi download,
- dodano `services/documents/document_signing_service.py` dla legacy uploadu podpisanego PDF-a glownego formularza,
- `routes/documents.py` deleguje pobieranie PDF-ow i upload podpisanego PDF-a do nowych serwisow,
- zapis podpisanego PDF-a nadal aktualizuje `signed_pdf_filename` oraz metadane w `SubmissionFile`,
- nie dodano nowych fallbackow nazw plikow; `SubmissionFile.storage_path` pozostaje zrodlem prawdy tam, gdzie istnieje.

Testy dodane lub zmienione w P3.2:

| Test | Zakres |
|---|---|
| `tests/test_document_split_services.py` | Delegacja download service, zapis podpisanego PDF-a przez signing service, metadane pliku i regresje service layer. |
| `tests/test_routes.py` | Regresje istniejacych URL-i pobran i uploadu podpisanego PDF-a. |
| `tests/test_runtime_legacy_imports.py` | Brak runtime importu `legacy_app.py` w dokumentach i adminie. |

Wynik testow P3.2:

```text
225 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Etap P3.3 - wydzielenie deklaracji i umow z routingu dokumentow

Wykonano bez migracji bazy, bez usuwania pol legacy, bez usuwania `legacy_app.py`, bez zmiany publicznych URL-i i bez zmiany lokalizacji dokumentow:

- dodano `services/documents/declaration_flow_service.py` dla formularza deklaracji i dodatkowych pol po akceptacji,
- dodano `services/documents/agreement_flow_service.py` dla generowania umow szkoleniowych i adaptera `training_agreement`,
- rozszerzono `services/documents/document_signing_service.py`, aby podpisana deklaracja i podpisana umowa przechodzily przez warstwe signing service,
- rozszerzono `services/documents/document_view_service.py` o skladanie widoku `documents_to_sign`,
- `routes/documents.py` pozostaje pojedynczym modulem blueprintu, ale jest ciensza warstwa HTTP: request, flash, redirect, render i wywolanie serwisu,
- zachowano import zgodnosci `from routes.documents import bp as documents_bp`,
- zachowano publiczne URL-e i nazwy endpointow,
- flow deklaracji nadal aktualizuje pola `declaration_*`, dodatkowe pola formularza, `data_json`, `process_status` i `workflow_step`,
- flow umow nadal aktualizuje `agreement_*` i `training_agreements` przez istniejacy `DocumentService.generate_documents_for_collection()`,
- `SubmissionFile.storage_path` nadal pozostaje zrodlem prawdy; fallback po nazwie nie zostal rozszerzony,
- runtime nadal nie importuje `legacy_app.py`.

Testy dodane lub zmienione w P3.3:

| Test | Zakres |
|---|---|
| `tests/test_document_split_services.py` | `DeclarationFlowService`, `AgreementFlowService`, delegacja signed uploadow przez `DocumentSigningService`, metadane i statusy legacy. |
| `tests/test_routes.py` | Regresje istniejacych URL-i deklaracji, dodatkowych pol, umow i podpisow. |
| `tests/test_runtime_legacy_imports.py` | Brak runtime importu `legacy_app.py`; nowy flow deklaracji przejmuje zaleznosc od `training_agreement_service`. |

Wynik testow P3.3:

```text
229 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Etap P3.4 - zamkniecie routingu dokumentow

Wykonano bez migracji bazy, bez usuwania pol legacy, bez usuwania `legacy_app.py`, bez zmiany publicznych URL-i i bez zmiany lokalizacji dokumentow.

Decyzja architektoniczna: `routes/documents.py` nie zostal zamieniony na pakiet `routes/documents/*` w P3.4. Powod:

- modul jest juz cienka warstwa HTTP i ma tylko 9 endpointow,
- konwersja na pakiet wymagalaby jednoczesnego usuniecia pliku `routes/documents.py` i utworzenia katalogu o tej samej nazwie,
- testy i raporty audytowe nadal celowo sprawdzaja `routes/documents.py`,
- dalszy zysk z podzialu bylby glownie organizacyjny, a ryzyko dotyczy rejestracji endpointow oraz importu `from routes.documents import bp as documents_bp`.

W P3.4 domknieto modul jako cienki adapter:

- dodano techniczny komentarz na gorze `routes/documents.py`,
- przeniesiono obsluge assetow Nextcloud do `DocumentDownloadService.prepare_asset()`,
- przeniesiono domyslna notyfikacje `AGREEMENT_SIGNED` do `AgreementFlowService`,
- `routes/documents.py` deleguje do `DeclarationFlowService`, `AgreementFlowService`, `DocumentDownloadService`, `DocumentSigningService` i `DocumentViewService`,
- publiczne URL-e i nazwy endpointow zostaly zachowane,
- `SubmissionFile.storage_path` nadal pozostaje zrodlem prawdy,
- fallback po nazwie nie zostal rozszerzony,
- runtime nadal nie importuje `legacy_app.py`.

Testy dodane lub zmienione w P3.4:

| Test | Zakres |
|---|---|
| `tests/test_routes.py` | `from routes.documents import bp as documents_bp`, rejestracja starych endpointow i `url_for()` dla publicznych sciezek. |
| `tests/test_runtime_legacy_imports.py` | `routes/documents.py` jako cienki adapter HTTP, brak lokalnych list/statusow i delegacja do serwisow. |
| `tests/test_document_split_services.py` | Regresje serwisow dokumentowych po przeniesieniu helperow. |

Wynik testow P3.4:

```text
231 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Etap P3.5 - decyzja i cleanup legacy_app.py

Wykonano bez migracji bazy, bez usuwania pol legacy, bez usuwania `legacy_app.py`, bez zmiany publicznych URL-i i bez zmiany lokalizacji dokumentow.

Decyzja architektoniczna: wybrano wariant A. `legacy_app.py` zostaje jawnie historycznym modulem zgodnosci, bo:

- plik ma nadal bezposredni entrypoint `if __name__ == "__main__"`,
- testy zgodnosci importuja wrappery legacy,
- historyczne endpointy moga nadal sluzyc diagnostyce poza runtime `create_app()`,
- usuniecie pliku powinno byc osobnym etapem po potwierdzeniu, ze nikt nie uruchamia go bezposrednio.

W P3.5:

- doprecyzowano naglowek `legacy_app.py`, ze runtime `create_app()` go nie importuje ani nie rejestruje jego endpointow,
- zachowano wrappery zgodnosci: `ensure_declaration_generated`, `generate_training_agreements_for_submission`, `build_training_agreement_filename`, `build_agreement_block_updates`, `build_declaration_form_definition`, `build_training_agreement_number`, `extract_training_selection`, `get_training_selection_field` oraz `LegacyPdfRenderAdapter`,
- usunieto oczywiscie martwy kod po delegujacych `return` w wrapperach,
- pozostawiono historyczne endpointy Flask oznaczone jako legacy-only,
- runtime nadal nie importuje `legacy_app.py`.

Testy dodane lub zmienione w P3.5:

| Test | Zakres |
|---|---|
| `tests/test_runtime_legacy_imports.py` | `create_app()` nie rejestruje view funkcji z `legacy_app.py`; importy legacy sa ograniczone do testow zgodnosci/audytu. |
| `tests/test_legacy_dependencies.py` | `legacy_app.py` ma naglowek historyczny i nie zawiera usunietych martwych blokow po wrapperach. |
| `tests/test_document_split_services.py`, `tests/test_training_agreements.py`, `tests/test_training_agreement_service.py` | Regresja wrapperow zgodnosci legacy. |

Wynik testow P3.5:

```text
235 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Etap P3.6 - domkniecie frontendu dokumentow

Wykonano bez migracji bazy, bez usuwania pol legacy, bez zmiany publicznych URL-i i bez zmiany logiki biznesowej dokumentow.

W P3.6:

- usunieto inline CSS z `templates/documents_to_sign.html`,
- `templates/documents_to_sign.html` laduje style przez `static/documents_to_sign.css`,
- `templates/documents_to_sign.html` laduje logike przez `static/documents_to_sign.js` z atrybutem `defer`,
- resztki inline `style="..."` zastapiono klasami CSS,
- ukrywanie/pokazywanie elementow w JS korzysta z klasy `.is-hidden`,
- frontend nie utrzymuje lokalnych list statusow koncowych ani odrzuconych,
- logika odrzucenia korzysta z backendowej flagi `data.is_rejected`,
- pozostale flagi widoku nadal pochodza z backendowego `DocumentViewService` oraz `services/status_catalog.py`,
- drag-and-drop uploadu, podmiana kafelka po pobraniu dokumentu i komunikaty uploadu pozostaja w `static/documents_to_sign.js`.

Testy dodane lub zmienione w P3.6:

| Test | Zakres |
|---|---|
| `tests/test_p2_1_frontend_assets.py` | Bloki `extra_css`/`extra_js`, obecność assetow, brak inline CSS, ladowanie JS przez `defer`, brak lokalnych list statusow. |
| `tests/test_routes.py` | Regresja widoku dokumentow do podpisu i publicznych endpointow dokumentow. |
| `tests/test_runtime_legacy_imports.py` | Brak runtime importu `legacy_app.py`. |

Wynik testow P3.6:

```text
236 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Etap P4.0 - przygotowanie migracji FormSubmission

Wykonano niedestrukcyjnie: bez usuwania kolumn legacy, bez zmiany publicznych URL-i, bez backfillu i bez zmiany odczytu starszych zgloszen.

W P4.0:

- pozostawiono `SubmissionFile` jako reprezentacje dokumentu i nie dodawano osobnej tabeli `SubmissionDocument`,
- rozszerzono `SubmissionFile` o metadane dokumentow: `original_filename`, `signature_status`, `signature_validation_result`, `agreement_number`, `training_key`, `generated_at`, `signed_at`, `updated_at`,
- dodano modele audytowe `SubmissionWorkflowEvent` i `SubmissionDecision`,
- dodano migracje `20260610_0009_p4_dual_write_audit_structures.py`,
- dodano `services/submission_document_service.py`, ktory traktuje `SubmissionFile` jako docelowe metadane dokumentow,
- przepieto dual-write metadanych dokumentow dla PDF formularza, podpisanego PDF formularza, deklaracji, podpisanych deklaracji, umow i umow szkoleniowych,
- dodano dual-write eventow workflow w `WorkflowService`,
- dodano audyt decyzji urzednika w `routes/admin/submissions.py`,
- odczyt dokumentow nadal preferuje `SubmissionFile.storage_path`, a legacy fallback po nazwie pozostaje tylko dotychczasowym fallbackiem,
- `EmailLog` pozostaje osobnym logiem maili, a mail decyzji nadal wysyla sie tylko przy faktycznej zmianie decyzji.

Testy dodane lub zmienione w P4.0:

| Test | Zakres |
|---|---|
| `tests/test_submission_repository.py` | Nowe metadane `SubmissionFile`, wiele dokumentow jednego zgloszenia, event workflow i audyt decyzji. |
| `tests/test_workflow_service.py` | Dual-write eventow workflow i domyslny aktor `system`. |
| `tests/test_admin_panel.py` | Audyt decyzji urzednika oraz brak podwojnej wysylki maila decyzji. |
| `tests/test_document_split_services.py`, `tests/test_routes.py`, `tests/test_form_submission.py` | Regresja dokumentow, routingu i publicznego zapisu formularza. |

Wynik testow P4.0:

```text
238 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Etap P4.1 - backfill metadanych legacy

Dodano bezpieczny mechanizm backfillu bez usuwania pol legacy, bez przelaczania globalnego odczytu i bez zmiany publicznych URL-i.

W P4.1:

- dodano skrypt `scripts/backfill_p4_metadata.py`,
- domyslnym trybem jest `dry-run`, a zapis wymaga jawnego `--apply`,
- skrypt obsluguje `--limit`, `--submission-id`, `--report`, `--database-url` i `--output-dir`,
- backfill uzupelnia metadane `SubmissionFile` na podstawie `pdf_filename`, `signed_pdf_filename`, `declaration_*`, `agreement_*` i `training_agreements`,
- backfill tworzy poczatkowe `SubmissionWorkflowEvent`, jezeli zgloszenie nie ma jeszcze eventow,
- backfill tworzy `SubmissionDecision` z danych legacy decyzji, jezeli mozna ja bezpiecznie odtworzyc,
- raportuje `created`, `updated`, `skipped_existing`, `missing_file`, `unsafe_path`, `ambiguous` i `error`,
- raport nie zawiera PESEL ani pelnych danych formularza,
- istniejace `SubmissionFile.storage_path` nie jest nadpisywane,
- brak pliku jest raportowany, ale nie przerywa backfillu,
- niebezpieczne nazwy plikow sa pomijane i raportowane,
- backfill jest idempotentny dla dokumentow, workflow i decyzji.

Przyklady:

```powershell
python scripts/backfill_p4_metadata.py --dry-run
python scripts/backfill_p4_metadata.py --apply
python scripts/backfill_p4_metadata.py --apply --limit 100
python scripts/backfill_p4_metadata.py --apply --submission-id <ID>
python scripts/backfill_p4_metadata.py --dry-run --report output/backfill_p4_report.json
```

Testy dodane lub zmienione w P4.1:

| Test | Zakres |
|---|---|
| `tests/test_backfill_p4_metadata.py` | Dry-run, apply, limit, `submission_id`, raport JSON, dokumenty legacy, podpisane dokumenty, wiele umow szkoleniowych, idempotencja, ochrona `storage_path`, workflow, decyzje i unsafe filename. |

Wynik testow P4.1:

```text
246 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Etap P4.2 - przelaczenie odczytu na nowe struktury

Przelaczono odczyt preferencyjnie na nowe struktury z bezpiecznym fallbackiem do pol legacy. Nie usunieto pol legacy, nie zmieniono URL-i i nie rozszerzono runtime fallbacku po nazwie pliku.

W P4.2:

- `DocumentService.build_documents_view()` przekazuje metadane `SubmissionFile` do `DocumentViewService`,
- `DocumentViewService` buduje wpisy dokumentow najpierw z `SubmissionFile`, a dopiero potem z pol legacy,
- dokumenty w widoku maja `source` oraz `used_legacy_fallback`,
- legacy fallback widoku dokumentow jest logowany ostrzezeniem,
- pobieranie dokumentow preferuje metadane po typie dokumentu i nazwie pliku, a `SubmissionFile.storage_path` pozostaje zrodlem prawdy,
- jesli metadane nie istnieja, pozostaje dotychczasowy fallback po nazwie pliku w `DocumentStorageService`,
- dodano `SubmissionWorkflowHistoryService`, ktory preferuje `SubmissionWorkflowEvent` i fallbackuje do `FormSubmission.process_status` oraz `workflow_step`,
- dodano `SubmissionDecisionService`, ktory preferuje `SubmissionDecision` i fallbackuje do pol legacy decyzji,
- panel admina w szczegolach zgloszenia pokazuje historie workflow i decyzji z nowych serwisow bez zmiany endpointow.

Testy dodane lub zmienione w P4.2:

| Test | Zakres |
|---|---|
| `tests/test_document_split_services.py` | Widok dokumentow preferuje `SubmissionFile`, oznacza/loguje fallback legacy, download nadal preferuje `storage_path`. |
| `tests/test_workflow_service.py` | Odczyt historii workflow preferuje eventy, fallback nie zapisuje nowych eventow, decyzje preferuja `SubmissionDecision`. |
| `tests/test_routes.py`, `tests/test_admin_panel.py` | Regresja publicznych URL-i dokumentow i panelu admina. |

Wynik testow P4.2:

```text
250 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Etap P4.3 - raportowanie fallbackow legacy

Dodano mechanizm obserwacji fallbackow legacy bez ich wylaczania i bez usuwania pol legacy.

W P4.3:

- dodano `services/legacy_fallback_report_service.py`,
- dodano skrypt `scripts/report_legacy_fallbacks.py`,
- raport obejmuje dokumenty, workflow i decyzje,
- raport liczy m.in. `using_new_metadata`, `using_legacy_fallback`, `missing_submission_file`, `missing_storage_path`, `missing_physical_file`, `missing_events`, `missing_decision` i `ambiguous`,
- `fallback_records` zawiera tylko dane techniczne: `submission_id`, obszar, typ fallbacku, typ dokumentu, nazwe pliku i powod,
- raport nie zapisuje PESEL, danych osobowych, adresow, `data_json` ani tresci maili,
- dodano opcjonalny zapis JSON przez `--report`,
- skrypt obsluguje `--limit`, `--submission-id`, `--database-url` i `--output-dir`,
- skrypt niczego nie zapisuje w bazie i nie wysyla maili,
- dodano flagi strict mode: `STRICT_DOCUMENT_METADATA_READ`, `STRICT_WORKFLOW_HISTORY_READ`, `STRICT_DECISION_AUDIT_READ`,
- strict mode jest domyslnie wylaczony i sluzy diagnostyce/testom,
- serwis raportowania jest dostepny w kontenerze aplikacji, ale nie zmienia publicznych URL-i ani biezacych fallbackow.

Przyklady:

```powershell
python scripts/report_legacy_fallbacks.py
python scripts/report_legacy_fallbacks.py --limit 100
python scripts/report_legacy_fallbacks.py --submission-id <ID>
python scripts/report_legacy_fallbacks.py --report output/legacy_fallback_report.json
python scripts/report_legacy_fallbacks.py --database-url <DATABASE_URL>
```

Testy dodane lub zmienione w P4.3:

| Test | Zakres |
|---|---|
| `tests/test_legacy_fallback_report.py` | Raport dokumentow, workflow i decyzji, JSON, filtry CLI, brak danych wrazliwych oraz strict mode. |

Wynik testow P4.3:

```text
257 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Etap P4.4 - kontrolowane ograniczanie fallbackow legacy

Dodano mechanizm kontrolowanego blokowania fallbackow legacy po obszarach, ale strict mode nadal jest domyslnie wylaczony.

W P4.4:

- runtime dokumentow respektuje `STRICT_DOCUMENT_METADATA_READ`; przy braku `SubmissionFile.storage_path` zwraca kontrolowany blad odczytu zamiast legacy lookup po nazwie,
- historia workflow respektuje `STRICT_WORKFLOW_HISTORY_READ`; przy braku `SubmissionWorkflowEvent` zwraca diagnostyczny wynik `strict_missing_workflow_events`,
- decyzje respektuja `STRICT_DECISION_AUDIT_READ`; przy decyzji istniejacej tylko w polach legacy zwracany jest diagnostyczny wynik `strict_missing_submission_decision`,
- brak strict mode pozostawia dotychczasowe fallbacki aktywne,
- dodano `LegacyFallbackReadinessService` do oceny gotowosci per obszar,
- dodano skrypt `scripts/check_legacy_fallback_readiness.py`,
- readiness CLI obsluguje `--area documents|workflow|decisions`, `--report`, `--database-url`, `--limit` i `--submission-id`,
- kody wyjscia readiness CLI: `0` gotowe, `1` sa blokery, `2` blad techniczny,
- nie usunieto `legacy_app.py`, kolumn legacy ani publicznych endpointow,
- nie dodano zadnych zapisow, wysylek maili ani migracji destrukcyjnych w checku readiness.

Przyklady:

```powershell
python scripts/check_legacy_fallback_readiness.py --area documents --database-url <DATABASE_URL>
python scripts/check_legacy_fallback_readiness.py --area workflow --report output/workflow_readiness.json
python scripts/check_legacy_fallback_readiness.py --area decisions --limit 100
```

Testy dodane lub zmienione w P4.4:

| Test | Zakres |
|---|---|
| `tests/test_legacy_fallback_readiness.py` | Readiness per obszar, brak danych wrazliwych, CLI i kody wyjscia. |
| `tests/test_document_split_services.py` | Strict dokumentow blokuje legacy lookup i nie czyta pliku po nazwie. |
| `tests/test_workflow_service.py` | Strict workflow/decyzji zwraca diagnostyke bez fallbacku legacy. |

Wynik testow P4.4:

```text
266 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Etap P4.5 - operacyjne wlaczanie strict mode

Dodano procedury i zabezpieczenia dla wlaczania strict mode osobno dla dokumentow, workflow i decyzji. Domyslne zachowanie aplikacji pozostaje bez zmian.

W P4.5:

- dodano flage `REQUIRE_STRICT_READINESS_CHECK=false`,
- aplikacja loguje `strict_mode_enabled`, gdy startuje z aktywna flaga strict,
- przy `REQUIRE_STRICT_READINESS_CHECK=true` logowany jest `strict_readiness_blocker`, ale aplikacja nie wykonuje ciezkiego skanu bazy przy starcie,
- zdarzenia strict maja spojne nazwy: `strict_document_metadata_missing`, `strict_workflow_events_missing`, `strict_submission_decision_missing`,
- readiness loguje `strict_readiness_blocker` dla obszarow z blokerami,
- `scripts/check_legacy_fallback_readiness.py --recommend` generuje plan rollout dla wszystkich obszarow,
- rekomendacje maja akcje `enable_strict` albo `keep_fallback`,
- dodano dokument `STRICT_MODE_ROLLOUT.md` z komendami readiness, monitoringiem i rollbackiem,
- rollback strict mode polega na wylaczeniu odpowiedniej flagi, bez migracji danych,
- nie usunieto pol legacy, nie zmieniono URL-i i nie rozszerzono fallbacku po nazwie pliku.

Testy dodane lub zmienione w P4.5:

| Test | Zakres |
|---|---|
| `tests/test_app_factory.py` | Domyslne flagi strict, logowanie aktywnych flag i opcjonalnej bramki readiness. |
| `tests/test_document_split_services.py` | Log strict dokumentow, brak danych wrazliwych, rollback do legacy lookup po wylaczeniu flagi. |
| `tests/test_workflow_service.py` | Log strict workflow/decyzji, rollback do fallbackow po wylaczeniu flag. |
| `tests/test_legacy_fallback_readiness.py` | Rekomendacje rollout i raport JSON bez danych wrazliwych. |

Wynik testow P4.5:

```text
273 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Etap P4.6 - stabilizacja strict mode i przygotowanie legacy cleanup

Dodano etap przygotowawczy do przyszlej decyzji o legacy cleanup. P4.6 nie usuwa pol legacy, nie usuwa `legacy_app.py`, nie zmienia URL-i i nie wykonuje destrukcyjnej migracji.

W P4.6:

- dodano `services/strict_mode_stabilization_service.py`,
- dodano skrypt `scripts/report_strict_mode_stabilization.py`,
- raport stabilizacji obejmuje dokumenty, workflow i decyzje,
- raport pokazuje `strict_enabled`, `readiness_ready`, `fallbacks_detected`, `strict_events_detected`, `migration_candidate`, `requires_backfill` i `recommended_action`,
- rekomendacje stabilizacji to `keep_fallback`, `enable_strict`, `stabilize` i `ready_for_legacy_removal`,
- raport nie zawiera PESEL, danych osobowych, adresow, `data_json` ani tresci maili,
- serwis stabilizacji jest dostepny w kontenerze aplikacji jako narzedzie raportowe,
- dodano `LEGACY_REMOVAL_CHECKLIST.md`,
- dodano `LEGACY_REMOVAL_MIGRATION_PLAN.md`,
- dodano `LEGACY_APP_RETIREMENT_PLAN.md`,
- zaktualizowano `STRICT_MODE_ROLLOUT.md` o etap stabilizacji po wlaczeniu strict.

Przyklady:

```powershell
python scripts/report_strict_mode_stabilization.py --area all --report output/strict_mode_stabilization.json
python scripts/report_strict_mode_stabilization.py --area documents --limit 100
python scripts/report_strict_mode_stabilization.py --area workflow --submission-id <ID>
```

Celowo nie wykonano:

- usuwania kolumn z `FormSubmission`,
- usuwania fallbackow runtime,
- usuwania `legacy_app.py`,
- destrukcyjnej migracji Alembic/SQL,
- cleanupu artefaktow repozytorium.

Testy dodane lub zmienione w P4.6:

| Test | Zakres |
|---|---|
| `tests/test_strict_mode_stabilization.py` | Raport stabilizacji, rekomendacje, CLI, filtry, JSON i brak zapisow w bazie. |
| `tests/test_legacy_cleanup_plans.py` | Istnienie i zakres checklisty, planu migracji legacy oraz planu `legacy_app.py`. |

Wynik testow P4.6:

```text
281 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Etap P4.6.1 - wyrownanie schematu i odpornosc raportow

Etap naprawczy po raporcie stabilizacji, ktory wykryl brak `submission_files.original_filename` w lokalnym schemacie PostgreSQL. Nie dodano destrukcyjnej migracji i nie usunieto legacy.

W P4.6.1:

- potwierdzono, ze migracja `20260610_0009_p4_dual_write_audit_structures.py` zawiera `original_filename` oraz pozostale kolumny P4 `SubmissionFile`,
- dodano `services/p4_schema_check_service.py`,
- dodano `scripts/check_p4_schema.py`,
- schema check sprawdza tabele `submission_files`, `submission_workflow_events`, `submission_decisions`,
- schema check zwraca `0` dla zgodnosci, `1` dla brakow schematu i `2` dla bledow technicznych,
- raport fallbackow wykonuje rollback sesji po bledach SQL i oznacza `schema_mismatch`,
- raport stabilizacji blokuje legacy removal przy bledzie schematu przez `requires_schema_upgrade=true`,
- obszary z bledem schematu dostaja `recommended_action=keep_fallback`,
- zapis decyzji urzednika zapisuje pola legacy przed dual-write audytu `SubmissionDecision`, a brak tabeli audytu nie powoduje 500,
- odczyty metadanych `SubmissionFile` w adminie i repozytorium zwracaja puste wyniki oraz loguja `schema_mismatch`, jezeli schemat P4 nie zostal jeszcze wyrownany,
- dodano dokument `P4_SCHEMA_CHECK.md` z kolejnoscia: schema check -> migracje -> backfill -> readiness -> stabilization -> decyzja.

W P4.6.1 nie wykonano:

- usuwania kolumn legacy,
- usuwania `legacy_app.py`,
- migracji destrukcyjnej,
- wlaczenia strict mode,
- zmiany URL-i lub endpointow.

Wynik testow P4.6.1:

```text
294 passed
```

Uruchomiona komenda:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Pozostawione na kolejne etapy

| Priorytet | Zadanie |
|---|---|
| P1 | Nie dzielic `routes/documents.py` bez nowej potrzeby funkcjonalnej; modul jest formalnie zamkniety jako cienki adapter HTTP. |
| P1 | Przed ewentualnym usunieciem `legacy_app.py` potwierdzic, ze nie jest uzywany jako bezposredni entrypoint produkcyjny. |
| P1 | Zamkniecie `legacy_app.py` jako modulu zgodnosci albo usuniecie po potwierdzeniu, ze nie jest entrypointem produkcyjnym. |
| P1 | Ewentualne przeniesienie wspolnych helperow admina z `routes/admin/__init__.py` do `services/admin_access_service.py`. |
| P1 | Uruchomic P4.1 na kopii bazy produkcyjnej w trybie `--dry-run` i przejrzec raport roznic przed `--apply`. |
| P2 | Po stabilizacji strict uruchomic `report_strict_mode_stabilization.py` i przejsc checklisty legacy removal przed jakakolwiek migracja destrukcyjna. |
| P3 | Porzadki repozytorium: `.coverage`, `.pytest_cache/`, `tmp/logos/`, `output/` po potwierdzeniu, ze nie sa fixture. |
