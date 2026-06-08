# Admin split plan

`routes/admin.py` jest nadal pojedynczym blueprintem. Ponizej jest mapa bez zmiany routingu ani URL-i.

## Wykonane w P1.2 i P1.3

| Element | Nowe miejsce | Uwagi |
| --- | --- | --- |
| Helpery logo: `safe_asset_filename`, `list_selectable_logos`, `list_active_logos`, `can_select_logo`, `can_select_active_logo` | `services/logo_service.py` | Funkcje w `routes/admin.py` pozostaly jako adaptery zgodnosci dla obecnego blueprintu. |
| Budowa stopki maila | `services/mail_dispatch_service.py` | `build_footer_html()` w adminie deleguje do fasady dispatch. |
| Helpery kontekstu maila: `build_mail_context`, `render_mail_text`, `preview_mail_context`, `mail_template_type_score` | `services/admin_mail_context_service.py` | Funkcje w `routes/admin.py` pozostaly jako adaptery, a wysylka admina zachowuje obecne URL-e i formularze. |

## Wykonane w P2.1

| Element | Nowe miejsce | Uwagi |
| --- | --- | --- |
| Helpery formularzy: `parse_uploaded_form_definition`, `normalize_admin_form_definition`, `validate_admin_form_config`, `build_form_definition_from_admin_form`, `sync_form_fields`, `detect_form_fields`, `normalize_field_stage`, `form_has_additional_fields` | `services/admin_form_service.py` | Serwis i testy zostaly dodane. Pelne przepiecie importow w `routes/admin.py` zostaje jako kolejny maly commit, zeby nie laczyc go z wydzieleniem assetow. |

## Docelowe obszary

| Obszar docelowy | Funkcje |
| --- | --- |
| `routes/admin/auth.py` | `admin_index`, `login`, `logout`, `load_current_user`, `login_required`, `role_required`, `get_current_user` |
| `routes/admin/dashboard.py` | `dashboard`, `submissions_all` |
| `routes/admin/forms.py` | `forms_list`, `form_delete`, `forms_upload`, `form_edit`, `form_toggle`, `form_fields` |
| `routes/admin/submissions.py` | `submissions_list`, `submission_detail`, `submission_decision_update`, `submission_value`, `build_filter_fields`, `filter_submissions`, `sort_submissions`, `admin_status_label` |
| `routes/admin/mail.py` | `submission_mail`, `submissions_mail_selected`, `mail_templates_list`, `mail_templates_index`, `mail_template_edit`, `mail_template_import_html`, `mail_template_import_zip`, `mail_footers_list`, `mail_footer_edit`, `send_admin_mail`, `send_selected_submission_mail`, `select_mail_template`, `send_mail_for_submission` |
| `routes/admin/logos.py` | `logos_list`, `logo_toggle`, `logo_edit`, `logo_asset` po utrwaleniu `services/logo_service.py` |
| `routes/admin/users.py` | `users_list`, `user_toggle_block`, `user_edit`, `accessible_form_ids`, `accessible_form_slugs`, `list_accessible_forms`, `ensure_form_access` |
| `services/admin_template_import_service.py` | `build_definition_from_html`, `build_definition_from_docx`, `read_uploaded_template_file`, `html_attr`, `humanize_field_name` po usunieciu ich z `admin_form_service.py` lub jako modul niskiego poziomu importowany przez `admin_form_service.py`. |

## Kolejnosc

1. Przepiac `routes/admin.py` na importy z `services/admin_form_service.py` i zostawic stare nazwy jako adaptery tylko tam, gdzie sa potrzebne.
2. Rozszerzyc `MailDispatchService` o logowanie `EmailLog` dopiero po stabilizacji serwisu kontekstu.
3. Dopiero potem rozbijac blueprint, bo obecny plik `routes/admin.py` blokuje utworzenie katalogu `routes/admin/` bez zmiany importow.
4. Zostawic nazwy endpointow bez zmian albo dodac aliasy kompatybilnosci.

## Ryzyka

- `routes/admin.py` jest plikiem, wiec utworzenie pakietu `routes/admin/` wymaga osobnego kroku importowego.
- Funkcje zalezne od `request`, `session`, `flash` i `g` powinny zostac w blueprintcie do czasu pelnych testow regresji.
- Mail admina miesza wybor szablonu, render, log i wysylke; przenosic go przez fasade `MailDispatchService`, a nie jednorazowo.
- `admin_form_service.py` zawiera teraz logike czysta i funkcje dotykajace modeli przez jawne argumenty; przepiecie routingu powinno byc testowane osobno dla uploadu, edycji workflow i synchronizacji pol.
