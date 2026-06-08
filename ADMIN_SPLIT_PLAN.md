# Admin split plan

`routes/admin/__init__.py` jest nadal pojedynczym blueprintem. Ponizej jest mapa bez zmiany routingu ani URL-i.

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

## Docelowe obszary

| Obszar docelowy | Funkcje |
| --- | --- |
| `routes/admin/auth.py` | `admin_index`, `login`, `logout`, `load_current_user`, `login_required`, `role_required`, `get_current_user` |
| `routes/admin/dashboard.py` | `dashboard`, `submissions_all` |
| `routes/admin/forms.py` | Wykonane w P2.6: `forms_list`, `form_delete`, `forms_upload`, `form_edit`, `form_toggle`, `form_fields`. |
| `routes/admin/submissions.py` | `submissions_list`, `submission_detail`, `submission_decision_update`, `submission_value`, `build_filter_fields`, `filter_submissions`, `sort_submissions`, `admin_status_label` |
| `routes/admin/mail.py` | `submission_mail`, `submissions_mail_selected`, `mail_templates_list`, `mail_templates_index`, `mail_template_edit`, `mail_template_import_html`, `mail_template_import_zip`, `mail_footers_list`, `mail_footer_edit`, `send_admin_mail`, `send_selected_submission_mail`, `select_mail_template`, `send_mail_for_submission` |
| `routes/admin/logos.py` | Wykonane w P2.5: `logos_list`, `logo_toggle`, `logo_edit`, `logo_asset`. |
| `routes/admin/users.py` | `users_list`, `user_toggle_block`, `user_edit`, `accessible_form_ids`, `accessible_form_slugs`, `list_accessible_forms`, `ensure_form_access` |
| `services/admin_template_import_service.py` | `build_definition_from_html`, `build_definition_from_docx`, `read_uploaded_template_file`, `html_attr`, `humanize_field_name` po usunieciu ich z `admin_form_service.py` lub jako modul niskiego poziomu importowany przez `admin_form_service.py`. |

## Kolejnosc

1. Kolejny rekomendowany obszar: `routes/admin/users.py`, bo endpointy uzytkownikow sa mniejsze i korzystaja z juz istniejacych helperow uprawnien.
2. Alternatywnie przeniesc `routes/admin/submissions.py`, jesli najpierw zostana utrzymane wspolne helpery filtrowania i statusow.
3. `routes/admin/mail.py` zostawic na pozniej, dopoki `MailDispatchService` nie ma pelnego logowania `EmailLog`.
4. Zostawic nazwy endpointow bez zmian albo dodac aliasy kompatybilnosci.

## Ryzyka

- `routes/admin/__init__.py` nadal zawiera duzy blueprint; przenoszenie endpointow do modulow potomnych wymaga ostroznego zarzadzania importem `bp`, dekoratorami i helperami auth.
- Funkcje zalezne od `request`, `session`, `flash` i `g` powinny zostac w blueprintcie do czasu pelnych testow regresji.
- Mail admina miesza wybor szablonu, render, log i wysylke; przenosic go przez fasade `MailDispatchService`, a nie jednorazowo.
- `admin_form_service.py` zawiera teraz logike czysta i funkcje dotykajace modeli przez jawne argumenty; przepiecie routingu powinno byc testowane osobno dla uploadu, edycji workflow i synchronizacji pol.
- Logo zapisuje pliki w `TEMP_DIR/logos`; przy dalszym podziale endpointow nie zmieniac sciezek storage ani sposobu serwowania istniejacych plikow.
