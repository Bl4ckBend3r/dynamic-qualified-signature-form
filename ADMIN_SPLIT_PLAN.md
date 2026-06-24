# Admin split plan

`routes/admin/__init__.py` nadal eksportuje wspolny blueprint `bp`, ale glowne obszary admina sa juz podzielone na moduly potomne. Ponizej jest mapa bez zmiany routingu ani URL-i.

## Wykonane w P1.2 i P1.3

| Element | Nowe miejsce | Uwagi |
| --- | --- | --- |
| Helpery logo: `safe_asset_filename`, `list_selectable_logos`, `list_active_logos`, `can_select_logo`, `can_select_active_logo` | `services/logo_service.py` | Funkcje w module admina pozostaly jako adaptery zgodnosci dla obecnego blueprintu. |
| Budowa stopki maila | `services/mail_dispatch_service.py` | `build_footer_html()` w adminie deleguje do fasady dispatch. |
| Helpery kontekstu maila: `build_mail_context`, `render_mail_text`, `preview_mail_context`, `mail_template_type_score` | `services/admin_mail_context_service.py` | Funkcje w module admina pozostaly jako adaptery, a wysylka admina zachowuje obecne URL-e i formularze. |

## Wykonane w P2.1

| Element | Nowe miejsce | Uwagi |
| --- | --- | --- |
| Helpery formularzy: `parse_uploaded_form_definition`, `normalize_admin_form_definition`, `validate_admin_form_config`, `build_form_definition_from_admin_form`, `sync_form_fields`, `detect_form_fields`, `normalize_field_stage`, `form_has_additional_fields` | `services/admin_form_service.py` | Serwis i testy zostaly dodane. |

## Wykonane w P2.2

| Element | Nowe miejsce | Uwagi |
| --- | --- | --- |
| Przepiecie modulu admina na helpery formularzy | `services/admin_form_service.py` | Lokalne duplikaty helperow zostaly usuniete z routingu; modul admina importuje funkcje z serwisu. |
| Importery HTML/DOCX i helpery niskiego poziomu | `services/admin_form_service.py` | `build_definition_from_html`, `build_definition_from_docx`, `html_attr`, `humanize_field_name`, `parse_workflow_json` pozostaja w serwisie formularzy. |

## Wykonane w P2.3

| Element | Nowe miejsce | Uwagi |
| --- | --- | --- |
| Logika listy i uploadu logo | `services/logo_service.py` | Modul admina zostal cienkim adapterem dla request, flash i redirect. |
| Logika edycji metadanych logo | `services/logo_service.py` | Endpoint `/admin/logos/<id>/edit` zachowal nazwe `admin.logo_edit`. |
| Logika dostepu do assetu logo | `services/logo_service.py` | Endpoint `/admin/logos/<id>/asset` zachowal nazwe `admin.logo_asset`; serwis decyduje, czy uzytkownik moze pobrac plik. |

W P2.3 nie utworzono jeszcze `routes/admin/logos.py`, bo projekt mial wtedy `routes/admin.py` jako plik, a `app.py` importowal `from routes.admin import bp as admin_bp`.

## Wykonane w P2.4

| Element | Nowe miejsce | Uwagi |
| --- | --- | --- |
| Modul admina | `routes/admin/__init__.py` | `routes/admin.py` zostal przeniesiony do pakietu bez zmiany zawartosci blueprintu. |
| Import blueprintu | `from routes.admin import bp` | Import uzywany przez `app.py` pozostaje zgodny. |

Endpointy nie zostaly jeszcze przeniesione do osobnych modulow. Ten etap tylko odblokowal bezpieczne dodawanie plikow takich jak `routes/admin/logos.py` i `routes/admin/forms.py`.

## Wykonane w P2.5

| Element | Nowe miejsce | Uwagi |
| --- | --- | --- |
| Endpoint listy i uploadu logo: `logos_list` | `routes/admin/logos.py` | URL `/admin/logos` i endpoint `admin.logos_list` pozostaly bez zmian. |
| Endpoint aktywacji/dezaktywacji: `logo_toggle` | `routes/admin/logos.py` | URL `/admin/logos/<id>/toggle` i endpoint `admin.logo_toggle` pozostaly bez zmian. |
| Endpoint edycji: `logo_edit` | `routes/admin/logos.py` | URL `/admin/logos/<id>/edit` i endpoint `admin.logo_edit` pozostaly bez zmian. |
| Endpoint assetu: `logo_asset` | `routes/admin/logos.py` | URL `/admin/logos/<id>/asset` i endpoint `admin.logo_asset` pozostaly bez zmian. |

`routes/admin/__init__.py` nadal eksportuje `bp` i importuje `routes.admin.logos` na koncu pliku, aby zarejestrowac trasy na wspolnym blueprintcie.

## Wykonane w P2.6

| Element | Nowe miejsce | Uwagi |
| --- | --- | --- |
| Endpoint listy formularzy: `forms_list` | `routes/admin/forms.py` | URL `/admin/forms` i endpoint `admin.forms_list` pozostaly bez zmian. |
| Endpoint usuwania: `form_delete` | `routes/admin/forms.py` | URL `/admin/forms/<id>/delete` i endpoint `admin.form_delete` pozostaly bez zmian. |
| Endpoint uploadu: `forms_upload` | `routes/admin/forms.py` | JSON, HTML i DOCX nadal korzystaja z `services/admin_form_service.py`. |
| Endpoint edycji: `form_edit` | `routes/admin/forms.py` | Workflow, logo, uprawnienia i synchronizacja pol pozostaly bez zmiany URL-i. |
| Endpoint toggle: `form_toggle` | `routes/admin/forms.py` | URL `/admin/forms/<id>/toggle` i endpoint `admin.form_toggle` pozostaly bez zmian. |
| Endpoint pol: `form_fields` | `routes/admin/forms.py` | Obsluga typow, sekcji, wymagania i `stage` pozostala w panelu formularzy. |

`routes/admin/__init__.py` nadal eksportuje `bp` i importuje `routes.admin.forms` na koncu pliku. Wspolne helpery dostepu, takie jak `ensure_form_access`, `list_accessible_forms`, `active_fields_for_form`, `normalize_slug` i adaptery logo, zostaly w pakiecie admina, bo nadal sa uzywane przez formularze oraz inne obszary panelu.

## Wykonane w P2.7

| Element | Nowe miejsce | Uwagi |
| --- | --- | --- |
| Auth: `admin_index`, `login`, `logout`, `load_current_user`, `login_required`, `role_required`, `get_current_user` | `routes/admin/auth.py` | `routes/admin/__init__.py` re-eksportuje dekoratory dla modulow potomnych. |
| Dashboard: `dashboard` | `routes/admin/dashboard.py` | URL `/admin/dashboard` i endpoint `admin.dashboard` pozostaly bez zmian. |
| Users: `users_list`, `user_toggle_block`, `user_edit` | `routes/admin/users.py` | URL-e i endpointy `admin.users_list`, `admin.user_toggle_block`, `admin.user_edit` pozostaly bez zmian. |
| Submissions: `submissions_all`, `submissions_list`, `submission_detail`, `submission_decision_update` | `routes/admin/submissions.py` | URL-e i nazwy endpointow pozostaly bez zmian. |
| Helpery list zgłoszen: `admin_status_label`, `submission_value`, `build_filter_fields`, `filter_submissions`, `sort_submissions` | `services/admin_submission_service.py` | Etykiety statusow przechodza przez `workflow_status_label()` i `services/status_catalog.py`. |

Wspolne helpery dostepu (`accessible_form_ids`, `accessible_form_slugs`, `list_accessible_forms`, `ensure_form_access`) pozostaja tymczasowo w `routes/admin/__init__.py`, bo sa wspoldzielone przez formularze, zgłoszenia, dashboard i mail. `routes/admin/mail.py` nadal jest odlozone do czasu pelniejszej centralizacji `MailDispatchService` z `EmailLog`.

## Wykonane w P2.8

| Element | Nowe miejsce | Uwagi |
| --- | --- | --- |
| Endpoint maila do zgloszenia: `submission_mail` | `routes/admin/mail.py` | URL `/admin/forms/<id>/submissions/<id>/mail` i endpoint `admin.submission_mail` pozostaly bez zmian. |
| Bulk mail: `submissions_mail_selected` | `routes/admin/mail.py` | URL `/admin/forms/<id>/submissions/mail-selected` i endpoint `admin.submissions_mail_selected` pozostaly bez zmian. |
| Szablony maili: `mail_templates_list`, `mail_templates_index`, `mail_template_edit`, import HTML i ZIP | `routes/admin/mail.py` | URL-e i nazwy endpointow pozostaly bez zmian. |
| Stopki maili: `mail_footers_list`, `mail_footer_edit` | `routes/admin/mail.py` | URL-e i nazwy endpointow pozostaly bez zmian. |
| Dispatch, render i log maili admina oraz decyzji | `services/mail_dispatch_service.py` | `EmailLog` jest zapisywany centralnie bez migracji bazy. |
| Mail decyzji urzednika | `routes/admin/submissions.py` + `MailDispatchService` | Wysylka tylko przy faktycznej zmianie decyzji. |

Po P2.8 w `routes/admin/__init__.py` pozostaja: wspolny blueprint `bp`, stale rol/statusow, `db_session_factory`, adaptery auth, helpery dostepu do formularzy, helpery pol/formularzy wspoldzielone przez moduly potomne, adaptery logo oraz cienkie adaptery zgodnosci `select_mail_template()` i `mail_template_type_score()`.

## Stan po P2.9

Admin jest juz podzielony na moduly:

- `routes/admin/auth.py`,
- `routes/admin/dashboard.py`,
- `routes/admin/forms.py`,
- `routes/admin/logos.py`,
- `routes/admin/submissions.py`,
- `routes/admin/users.py`,
- `routes/admin/mail.py`.

`routes/admin/__init__.py` pozostaje wspolnym punktem blueprintu i re-eksportu helperow. Nadal sa tam tylko helpery wspolne dla kilku modulow: `db_session_factory`, role/dekoratory auth, helpery dostepu do formularzy, helpery pol formularza oraz cienkie adaptery zgodnosci maili/logo. Nie sa juz traktowane jako pozostawiony etap podzialu endpointow.

## Stan po P3.0

Admin pozostaje podzielony na moduly potomne i P3.0 nie zmienia routingu panelu. Biezace dalsze prace dotycza legacy/dokumentow: orchestration deklaracji i umow jest juz poza `legacy_app.py`, a runtime fabryki aplikacji nie importuje legacy.

## Stan po P3.1

P3.1 nie zmienia podzialu admina ani routingu panelu. Dalsze prace pozostaja poza podzialem admina i dotycza zamkniecia `legacy_app.py` oraz dalszego porzadkowania dokumentow.

## Stan po P3.2

P3.2 nie zmienia podzialu admina ani routingu panelu. Zmiany dotycza dokumentow: pobieranie PDF-ow i legacy upload podpisanego PDF-a glownego formularza deleguja do nowych serwisow w `services/documents`. Admin pozostaje bez zmian poza utrzymaniem braku runtime importu `legacy_app.py`.

## Stan po P3.3

P3.3 nie zmienia podzialu admina ani routingu panelu. Zmiany dotycza dokumentow: flow deklaracji, dodatkowych pol, umow, podpisow i view modelu dokumentow deleguja do serwisow w `services/documents`.

## Stan po P3.4

P3.4 nie zmienia podzialu admina ani routingu panelu. Dotyczy wylacznie routingu dokumentow: `routes/documents.py` zostal formalnie zamkniety jako cienki modul HTTP i nie zostal zamieniony na pakiet.

## Stan po P3.5

P3.5 nie zmienia podzialu admina ani routingu panelu. Dotyczy wylacznie decyzji i cleanupu `legacy_app.py`; runtime admina nadal nie importuje legacy.

## Stan po P3.6

P3.6 nie zmienia podzialu admina ani routingu panelu. Dotyczy wylacznie frontendu dokumentow: `templates/documents_to_sign.html` korzysta z zewnetrznych assetow CSS/JS.

## Stan po P4.0

P4.0 nie zmienia podzialu admina ani publicznych URL-i panelu. Endpoint decyzji urzednika nadal znajduje sie w `routes/admin/submissions.py`; poza aktualizacja pol legacy dodaje teraz audyt `SubmissionDecision`. Wysylka maila decyzji nadal przechodzi przez `MailDispatchService` i jest uruchamiana tylko przy faktycznej zmianie decyzji.

## Stan po P4.1

P4.1 nie zmienia podzialu admina ani routingu panelu. Backfill decyzji dziala przez `scripts/backfill_p4_metadata.py`; endpoint decyzji urzednika pozostaje bez zmian w `routes/admin/submissions.py` i nadal odpowiada za biezace decyzje uzytkownika.

## Stan po P4.2

P4.2 nie zmienia podzialu admina ani routingu panelu. Szczegol zgloszenia czyta historie workflow przez `SubmissionWorkflowHistoryService` i decyzje przez `SubmissionDecisionService`, preferujac nowe tabele z fallbackiem do `FormSubmission`. Endpoint decyzji nadal pozostaje w `routes/admin/submissions.py`.

## Stan po P4.3

P4.3 nie zmienia routingu admina. Dodano techniczny serwis raportowania fallbackow legacy dostepny w kontenerze aplikacji, ale bez nowego dashboardu i bez zmiany endpointow panelu. Diagnostyka szczegolu zgloszenia z P4.2 pozostaje bez zmian.

## Stan po P4.4

P4.4 nie zmienia routingu admina ani podzialu modulow panelu. Serwisy szczegolu zgloszenia respektuja teraz flagi strict dla workflow i decyzji, zwracajac diagnostyczny wynik przy braku nowych rekordow zamiast korzystac z legacy fallbacku. Readiness check pozostaje skryptem technicznym, bez nowego widoku admina.

## Stan po P4.5

P4.5 nie zmienia routingu admina ani podzialu modulow panelu. Dodano operacyjne logowanie strict mode i plan rollout przez skrypt techniczny `scripts/check_legacy_fallback_readiness.py --recommend`. Nie dodano nowych endpointow admina i nie zmieniono istniejacych nazw endpointow.

## Stan po P4.6

P4.6 nie zmienia routingu admina, nie dodaje endpointow panelu i nie zmienia modulow admina. Dodano wylacznie techniczny raport stabilizacji strict mode oraz dokumenty planistyczne dotyczace legacy cleanup.

## Docelowe obszary

| Obszar docelowy | Funkcje |
| --- | --- |
| `routes/admin/auth.py` | Wykonane w P2.7: `admin_index`, `login`, `logout`, `load_current_user`, `login_required`, `role_required`, `get_current_user`. |
| `routes/admin/dashboard.py` | Wykonane w P2.7: `dashboard`. |
| `routes/admin/forms.py` | Wykonane w P2.6: `forms_list`, `form_delete`, `forms_upload`, `form_edit`, `form_toggle`, `form_fields`. |
| `routes/admin/submissions.py` | Wykonane w P2.7: `submissions_all`, `submissions_list`, `submission_detail`, `submission_decision_update`. |
| `routes/admin/mail.py` | Wykonane w P2.8: `submission_mail`, `submissions_mail_selected`, `mail_templates_list`, `mail_templates_index`, `mail_template_edit`, `mail_template_import_html`, `mail_template_import_zip`, `mail_footers_list`, `mail_footer_edit`; cienkie adaptery wysylki deleguja do `MailDispatchService`. |
| `routes/admin/logos.py` | Wykonane w P2.5: `logos_list`, `logo_toggle`, `logo_edit`, `logo_asset`. |
| `routes/admin/users.py` | Wykonane w P2.7: `users_list`, `user_toggle_block`, `user_edit`. |
| `services/admin_submission_service.py` | Wykonane w P2.7: helpery filtrowania, sortowania i etykiet statusow zgłoszen. |
| `services/admin_template_import_service.py` | `build_definition_from_html`, `build_definition_from_docx`, `read_uploaded_template_file`, `html_attr`, `humanize_field_name` po usunieciu ich z `admin_form_service.py` lub jako modul niskiego poziomu importowany przez `admin_form_service.py`. |

## Kolejnosc

1. Dokumenty: wydzielic pozostale route/helpery deklaracji i umow bez zmiany publicznych URL-i; dopiero potem rozwazyc pakiet `routes/documents/*`.
2. Legacy: zamknac `legacy_app.py` jako modul zgodnosci albo usunac po potwierdzeniu, ze nie jest entrypointem produkcyjnym.
3. Migracja: przygotowac i wykonac osobny etap migracyjny dopiero po zatwierdzeniu zakresu zmian bazy.
4. Repo cleanup: usunac artefakty i duplikaty dopiero po stabilizacji legacy/dokumentow.
5. Rozwazyc przeniesienie wspolnych helperow dostepu do `services/admin_access_service.py`, jesli beda dalej uzywane przez kilka modulow.

## Ryzyka

- `routes/admin/__init__.py` nadal zawiera wspolne helpery dostepu; ewentualne przeniesienie do `services/admin_access_service.py` powinno byc osobnym etapem.
- Funkcje zalezne od `request`, `session`, `flash` i `g` powinny zostac w blueprintcie do czasu pelnych testow regresji.
- Mail admina przechodzi juz przez `MailDispatchService`; dalsze prace powinny utrzymac `EmailLog` w serwisie, a endpointy zostawic jako warstwe request/flash/redirect.
- `admin_form_service.py` zawiera teraz logike czysta i funkcje dotykajace modeli przez jawne argumenty; przepiecie routingu powinno byc testowane osobno dla uploadu, edycji workflow i synchronizacji pol.
- Logo zapisuje pliki w `TEMP_DIR/logos`; przy dalszym podziale endpointow nie zmieniac sciezek storage ani sposobu serwowania istniejacych plikow.
