from pathlib import Path
from types import SimpleNamespace

import pytest

from database import create_session_factory
from models import Form, Logo, SiteFooter, User
from services.footer_logo_service import build_site_footer_view, normalize_social_links, resolve_footer_logo_url
from test_admin_panel import admin_app, admin_client  # noqa: F401
from werkzeug.security import generate_password_hash


def create_form(app, *, slug: str, name: str) -> int:
    with create_session_factory(app.config["DATABASE_URL"])() as db:
        form = Form(slug=slug, name=name, title=name, definition_json={"title": name, "fields": []})
        db.add(form)
        db.commit()
        return form.id


def create_user(app, *, role: str = "super_admin") -> int:
    with create_session_factory(app.config["DATABASE_URL"])() as db:
        user = User(
            email="admin@example.com",
            password_hash=generate_password_hash("secret"),
            role=role,
            is_active=True,
            is_blocked=False,
        )
        db.add(user)
        db.commit()
        return user.id


def login(client):
    html = client.get("/admin/").get_data(as_text=True)
    token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    return client.post("/admin/", data={"email": "admin@example.com", "password": "secret", "csrf_token": token})


def _logo(name: str = "Własne logo"):
    return SimpleNamespace(id=41, active=True, name=name, filename="own.png")


def _footer(**changes):
    values = {
        "logo_id": 41,
        "logo": _logo(),
        "logo_path": "https://legacy.example/fallback.png",
        "logo_position": "top",
        "logo_alignment": "left",
        "logo_width": None,
        "logo_height": None,
        "html_body": "<p>Treść stopki</p>",
        "left_html": None,
        "right_html": None,
        "layout": "two_columns",
        "social_links": None,
        "social_position": "left",
        "social_icon_style": "gold",
        "social_show_labels": False,
        "is_active": True,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_site_footer_logo_source_is_logo_id_then_path_then_none():
    own = resolve_footer_logo_url(_footer(), lambda logo: f"/assets/{logo.filename}", allow_relative=True)
    path = resolve_footer_logo_url(
        _footer(logo_id=None, logo=None, logo_path="https://example.test/footer.png"),
        allow_relative=True,
    )
    none = resolve_footer_logo_url(
        _footer(logo_id=None, logo=None, logo_path=r"C:\private\lubuskie.png"),
        allow_relative=True,
    )

    assert own == "/assets/own.png"
    assert path == "https://example.test/footer.png"
    assert none == ""


@pytest.mark.parametrize("position", ["top", "bottom", "left", "right"])
def test_site_footer_view_keeps_configured_logo_position(position):
    view = build_site_footer_view(
        _footer(logo_position=position, logo_alignment="right", logo_width=240, logo_height=80),
        lambda logo: f"/assets/{logo.filename}",
    )

    assert view["logo_position"] == position
    assert view["logo_alignment"] == "right"
    assert 'src="/assets/own.png"' in view["logo_html"]
    assert "width:240px" in view["logo_html"]
    assert "height:80px" in view["logo_html"]


def test_site_footer_inline_renders_only_at_placeholder_and_nowhere_when_missing():
    inline = build_site_footer_view(
        _footer(logo_position="inline", html_body="<p>Przed {{ footer_logo }} po.</p>"),
        lambda logo: f"/assets/{logo.filename}",
    )
    missing = build_site_footer_view(
        _footer(logo_position="inline", html_body="<p>Bez znacznika.</p>"),
        lambda logo: f"/assets/{logo.filename}",
    )

    assert inline["logo_html"] == ""
    assert 'src="/assets/own.png"' in inline["content_html"]
    assert "{{ footer_logo }}" not in inline["content_html"]
    assert "own.png" not in missing["content_html"]
    assert missing["logo_html"] == ""


def test_public_site_footer_uses_only_site_footer_logo(admin_app, admin_client):
    form_id = create_form(admin_app, slug="separate-footer", name="Separate footer")
    logo_dir = Path(admin_app.config["TEMP_DIR"])
    site_path = logo_dir / "site-footer.png"
    form_path = logo_dir / "form-logo.png"
    site_path.write_bytes(b"site")
    form_path.write_bytes(b"form")
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        site_logo = Logo(name="Site footer", filename=site_path.name, storage_path=str(site_path), mime_type="image/png", active=True)
        form_logo = Logo(name="Form", filename=form_path.name, storage_path=str(form_path), mime_type="image/png", active=True)
        db.add_all([site_logo, form_logo])
        db.flush()
        db.get(Form, form_id).logo_id = form_logo.id
        db.add(SiteFooter(
            name="Public footer",
            html_body="<p>Projekt testowy</p>",
            logo_id=site_logo.id,
            logo_position="right",
            logo_alignment="center",
            logo_width=220,
            is_active=True,
        ))
        db.commit()

    html = admin_client.get("/").get_data(as_text=True)
    footer_html = html.split('<footer class="site-footer"', 1)[1].split("</footer>", 1)[0]

    assert "site-footer.png" in footer_html
    assert footer_html.count("site-footer.png") == 1
    assert "form-logo.png" not in footer_html
    assert "images/logo.png" not in footer_html
    assert 'data-logo-position="right"' in footer_html
    assert "width:220px" in footer_html


def test_site_footer_without_logo_has_no_lubuskie_or_broken_image(admin_app, admin_client):
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        db.add(SiteFooter(name="No logo", html_body="<p>Stopka bez logo</p>", logo_id=None, logo_path="", is_active=True))
        db.commit()

    html = admin_client.get("/").get_data(as_text=True)
    footer_html = html.split('<footer class="site-footer"', 1)[1].split("</footer>", 1)[0]

    assert "Stopka bez logo" in footer_html
    assert "<img" not in footer_html
    assert "LUBUSKIEGO" not in footer_html.upper()
    assert "logo-lubuskie" not in footer_html.lower()


def test_admin_site_footer_preview_matches_public_source_and_position(admin_app, admin_client):
    create_user(admin_app)
    logo_path = Path(admin_app.config["TEMP_DIR"]) / "matching-footer.png"
    logo_path.write_bytes(b"logo")
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        logo = Logo(name="Matching", filename=logo_path.name, storage_path=str(logo_path), mime_type="image/png", active=True)
        db.add(logo)
        db.flush()
        db.add(SiteFooter(
            name="Matching",
            html_body="<p>Ta sama treść</p>",
            logo_id=logo.id,
            logo_position="bottom",
            social_links=[{"platform": "facebook", "url": "https://facebook.example/matching", "label": "Facebook", "is_active": True}],
            social_position="bottom",
            is_active=True,
        ))
        db.commit()
    login(admin_client)

    admin_html = admin_client.get("/admin/site/footer").get_data(as_text=True)
    public_html = admin_client.get("/").get_data(as_text=True)
    preview = admin_html.split('data-site-footer-preview>', 1)[1].split("</section>", 1)[0]
    public_footer = public_html.split('<footer class="site-footer"', 1)[1].split("</footer>", 1)[0]

    for rendered in (preview, public_footer):
        assert "matching-footer.png" in rendered
        assert "Ta sama treść" in rendered
        assert 'data-logo-position="bottom"' in rendered
        assert "facebook.example/matching" in rendered
        assert "site-footer__social-icon" in rendered


@pytest.mark.parametrize(
    ("layout", "left", "right"),
    [
        ("single", "Lewa", ""),
        ("two_columns", "Lewa", "Prawa"),
        ("left_logo_right_content", "", "Prawa"),
        ("left_content_right_logo", "Lewa", ""),
    ],
)
def test_site_footer_view_supports_all_column_layouts(layout, left, right):
    view = build_site_footer_view(_footer(layout=layout, left_html="<p>Lewa</p>", right_html="<p>Prawa</p>"))

    assert view["layout"] == layout
    assert left in view["left_html"]
    assert right in view["right_html"]


def test_site_footer_columns_fall_back_to_legacy_body_and_sanitize_html():
    view = build_site_footer_view(_footer(left_html="", right_html="", html_body='<p>Stara</p><script>alert(1)</script>'))

    assert "Stara" in view["left_html"]
    assert "<script" not in view["left_html"]


def test_social_links_are_active_sorted_accessible_and_safe():
    view = build_site_footer_view(_footer(
        social_position="bottom",
        social_icon_style="brand",
        social_links=[
            {"platform": "youtube", "url": "https://youtube.example/test", "label": "Kanał", "active": True, "sort_order": 20},
            {"platform": "facebook", "url": "https://facebook.example/test", "label": "Profil", "active": True, "sort_order": 10},
            {"platform": "x", "url": "javascript:alert(1)", "active": True, "sort_order": 5},
            {"platform": "instagram", "url": "https://instagram.example/test", "active": False, "sort_order": 1},
        ],
    ))
    html = str(view["social_bottom_html"])

    assert html.index("facebook.example") < html.index("youtube.example")
    assert "instagram.example" not in html
    assert "javascript:" not in html
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html
    assert 'aria-label="Profil"' in html
    assert '<span class="site-footer__social-icon" aria-hidden="true">' in html
    assert "site-footer__social-label-visible" not in html
    assert ">Profil</span>" not in html
    assert "site-footer__social--brand" in html


def test_social_links_can_explicitly_show_labels_next_to_icons():
    view = build_site_footer_view(_footer(
        social_show_labels=True,
        social_links=[{"platform": "facebook", "url": "https://facebook.example/page", "label": "Facebook", "is_active": True}],
    ))
    html = str(view["social_left_html"])

    assert "site-footer__social-link--with-label" in html
    assert '<span class="site-footer__social-label-visible">Facebook</span>' in html
    assert 'aria-label="Facebook"' in html


def test_inline_social_placeholder_and_missing_placeholder_fallback():
    links = [{"platform": "www", "url": "https://example.test", "active": True, "sort_order": 1}]
    inline = build_site_footer_view(_footer(left_html="<p>{{ footer_social_links }}</p>", social_links=links, social_position="inline"))
    fallback = build_site_footer_view(_footer(left_html="<p>Bez znacznika</p>", social_links=links, social_position="inline"))

    assert "site-footer__social" in inline["left_html"]
    assert not inline["social_bottom_html"]
    assert "site-footer__social" in fallback["social_bottom_html"]


@pytest.mark.parametrize(
    ("position", "slot"),
    [("left", "social_left_html"), ("right", "social_right_html"), ("bottom", "social_bottom_html")],
)
def test_social_links_render_in_configured_slot(position, slot):
    links = [{"platform": "facebook", "url": "https://facebook.example/page", "is_active": True, "sort_order": 1}]
    view = build_site_footer_view(_footer(social_links=links, social_position=position))

    assert "site-footer__social" in view[slot]
    for other in {"social_left_html", "social_right_html", "social_bottom_html"} - {slot}:
        assert not view[other]


def test_site_footer_css_contains_two_column_mobile_breakpoint():
    css = (Path(__file__).parents[1] / "static" / "style.css").read_text(encoding="utf-8")

    assert ".site-footer__columns" in css
    assert ".site-footer__social-link" in css
    assert ".site-footer__social-icon" in css
    assert ".site-footer__social--gold" in css
    assert "gap: 12px" in css
    assert "@media (max-width: 700px)" in css
    mobile_css = css.split("@media (max-width: 700px)", 1)[1]
    assert "grid-template-columns: 1fr" in mobile_css


def test_social_validation_rejects_invalid_active_rows_but_ignores_empty_inactive_row():
    assert normalize_social_links([{"platform": "", "url": "", "active": False}], strict=True) == []
    with pytest.raises(ValueError, match="http:// lub https://"):
        normalize_social_links([{"platform": "facebook", "url": "javascript:alert(1)", "active": True}], strict=True)
    with pytest.raises(ValueError, match="Podaj adres URL"):
        normalize_social_links([{"platform": "facebook", "url": "", "active": True}], strict=True)


def test_admin_can_save_column_layout_and_multiple_social_links(admin_app, admin_client):
    create_user(admin_app)
    login(admin_client)
    page = admin_client.get("/admin/site/footer").get_data(as_text=True)
    token = page.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post("/admin/site/footer", data={
        "csrf_token": token,
        "name": "Stopka testowa",
        "is_active": "on",
        "layout": "two_columns",
        "left_html": "<p>Lewa</p>",
        "right_html": "<p>Prawa</p>",
        "html_body": "<p>Legacy</p>",
        "logo_id": "",
        "logo_position": "top",
        "logo_alignment": "left",
        "logo_width": "",
        "logo_height": "",
        "social_position": "bottom",
        "social_icon_style": "light",
        "social_show_labels": "on",
        "social_platform": ["youtube", "facebook"],
        "social_url": ["https://youtube.example/channel", "https://facebook.example/page"],
        "social_label": ["Kanał", "Profil"],
        "social_sort_order": ["20", "10"],
        "social_active_index": ["0", "1"],
    })

    assert response.status_code == 302
    with create_session_factory(admin_app.config["DATABASE_URL"])() as db:
        footer = db.query(SiteFooter).one()
        assert footer.layout == "two_columns"
        assert footer.left_html == "<p>Lewa</p>"
        assert [link["platform"] for link in footer.social_links] == ["facebook", "youtube"]
        assert footer.social_position == "bottom"
        assert footer.social_icon_style == "light"
        assert footer.social_show_labels is True


def test_admin_rejects_unsafe_active_social_url(admin_app, admin_client):
    create_user(admin_app)
    login(admin_client)
    page = admin_client.get("/admin/site/footer").get_data(as_text=True)
    token = page.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    response = admin_client.post("/admin/site/footer", data={
        "csrf_token": token,
        "name": "Stopka testowa",
        "layout": "single",
        "logo_position": "top",
        "logo_alignment": "left",
        "social_position": "left",
        "social_icon_style": "gold",
        "social_platform": "facebook",
        "social_url": "javascript:alert(1)",
        "social_label": "Profil",
        "social_sort_order": "1",
        "social_active_index": "0",
    })

    assert response.status_code == 400
    assert "musi zaczynać się od http:// lub https://" in response.get_data(as_text=True)


def test_standard_admin_can_open_site_footer_editor(admin_app, admin_client):
    create_user(admin_app, role="admin")
    login(admin_client)

    response = admin_client.get("/admin/site/footer")

    assert response.status_code == 200
    assert "Układ stopki" in response.get_data(as_text=True)
