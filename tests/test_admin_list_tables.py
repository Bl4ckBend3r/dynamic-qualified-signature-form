from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _template(path: str) -> str:
    return (PROJECT_ROOT / "templates" / "admin" / path).read_text(encoding="utf-8")


def test_submission_lists_have_column_filters_copyable_ids_and_sticky_action_menus():
    for path in ("submissions/list.html", "submissions/all.html"):
        source = _template(path)
        assert "admin-column-filters" in source
        assert 'name="submission_id"' in source
        assert 'name="full_name"' in source
        assert 'name="workflow_stage"' in source
        assert "data-copy-id" in source
        assert "data-admin-action-toggle" in source
        assert "data-admin-action-menu" in source
        assert 'class="actions-column"' in source


def test_form_submission_list_has_compact_filters_single_select_all_and_grouped_columns():
    source = _template("submissions/list.html")

    assert source.count('id="select-all-submissions"') == 1
    assert 'id="select-all-assignments"' not in source
    assert 'aria-label="Zaznacz wszystkie zgłoszenia na stronie"' in source
    assert 'data-advanced-filters' in source
    assert 'aria-controls="submission-additional-filters"' in source
    assert 'aria-expanded=' in source
    for field in (
        "q", "status", "date_from", "date_to", "field", "operator", "value",
        "value_to", "sort", "direction", "checklist_status", "queue", "assignee",
        "priority",
    ):
        assert f'name="{field}"' in source
    for heading in ("Osoba", "Kontakt", "Prowadzący", "Priorytet / termin", "Etap workflow"):
        assert heading in source
    assert "admin-submission-bulk" in source
    assert "admin-table-wrap" in source
    assert 'aria-label="Kopiuj pełne ID"' in source


def test_checklist_edit_forms_submit_exactly_one_semantic_action():
    source = _template("forms/checklists.html")

    assert 'type="hidden"\n               name="action"\n               value="update_checklist"' not in source
    assert 'type="hidden"\n               name="action"\n               value="update_item"' not in source
    for action in ("update_checklist", "delete_checklist", "update_item", "delete_item"):
        assert f'name="action"\n            value="{action}"' in source
    assert 'value="create_checklist"' in source
    assert 'value="add_item"' in source


def test_dashboard_groups_every_existing_metric_and_queue():
    source = _template("dashboard.html")

    for heading in ("Ogólne", "E-mail", "SMTP", "Kolejki spraw"):
        assert f">{heading}<" in source
    for metric in (
        "Liczba formularzy",
        "Liczba zgłoszeń",
        "Oczekujące zgłoszenia",
        "Liczba dokumentów",
        "Udane wysyłki e-mail",
        "Nieudane wysyłki e-mail",
        "Ostatnia próba wysyłki",
        "Udane testy SMTP",
        "Nieudane testy SMTP",
        "Ostatnia próba SMTP",
    ):
        assert metric in source
    for queue in ("mine", "unassigned", "overdue", "sla_overdue", "sla_today", "sla_24h", "sla_48h", "requiring_decision", "waiting_office_signature"):
        assert f"queue='{queue}'" in source


def test_all_submission_filters_are_labelled_and_keep_request_names():
    source = _template("submissions/all.html")

    for label in ("Szukaj", "Status", "Stan workflow", "Stan checklisty", "Data od", "Data do", "Sortowanie", "Kierunek", "Kolejka", "Prowadzący", "Priorytet"):
        assert f"<span>{label}</span>" in source
    for field in ("q", "status", "workflow_stage", "checklist_status", "date_from", "date_to", "sort", "direction", "queue", "assignee", "priority"):
        assert f'name="{field}"' in source
    assert "admin-filter-more" in source
    assert "Więcej filtrów" in source
    assert 'class="admin-button" type="submit">Filtruj' in source
    assert "Wyczyść filtry" in source


def test_submission_detail_uses_data_cards_and_keeps_full_audit_hash_available():
    source = _template("submissions/detail.html")

    assert "admin-data-list" in source
    assert "admin-data-row" in source
    assert "admin-data-list--declarations" in source
    assert "Szczegóły audytowe" in source
    assert "Pełny SHA-256" in source
    assert 'data-copy-value="{{ consent_hash }}"' in source
    assert "consent_hash[:8]" in source
    assert "consent_hash[-7:]" in source
    assert "can_view_sensitive_data" in source


def test_form_list_has_safe_filters_sorting_pagination_and_action_menu():
    source = _template("forms/list.html")

    for field in ("name", "slug", "status", "public", "label", "date_from", "date_to"):
        assert f'name="{field}"' in source
    assert "sort_urls[field]" in source
    assert "pagination_urls.previous" in source
    assert "data-admin-action-toggle" in source
    assert "admin.form_toggle" in source
    assert "data-admin-confirm-message" in source


def test_admin_action_menu_is_accessible_and_not_clipped_by_table_scroll():
    base = _template("base.html")
    css = (PROJECT_ROOT / "static" / "css" / "admin.css").read_text(encoding="utf-8")

    assert 'event.key === "Escape"' in base
    assert 'event.key === "ArrowDown"' in base
    assert 'aria-expanded' in base
    assert 'aria-haspopup="menu"' in _template("forms/list.html")
    assert "document.body.appendChild(menu)" in base
    assert "menu.replaceWith(placeholder)" in base
    scroll_listener = base.split('window.addEventListener("scroll",', 1)[1].split(
        'window.addEventListener("pagehide"', 1
    )[0]
    assert "closeActionMenu();" in scroll_listener
    assert "}, true);" in scroll_listener
    assert "navigator.clipboard.writeText" in base
    assert 'closest("[data-copy-id], [data-copy-value]")' in base
    assert "copyButton.dataset.copyValue || copyButton.dataset.copyId" in base
    action_rule = css.split(".admin-action-menu {", 1)[1].split("}", 1)[0]
    assert "position: fixed" in action_rule
    assert "z-index: 10000" in action_rule
    assert "overflow-x: visible" in action_rule
    action_column_rule = css.split(".admin-table .actions-column {", 1)[1].split("}", 1)[0]
    assert "position: sticky" in action_column_rule
    assert "right: 0" in action_column_rule
