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
    assert 'aria-expanded' in base
    assert "navigator.clipboard.writeText" in base
    action_rule = css.split(".admin-action-menu {", 1)[1].split("}", 1)[0]
    assert "position: fixed" in action_rule
    action_column_rule = css.split(".admin-table .actions-column {", 1)[1].split("}", 1)[0]
    assert "position: sticky" in action_column_rule
    assert "right: 0" in action_column_rule
