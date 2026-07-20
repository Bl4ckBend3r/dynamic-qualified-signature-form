from pathlib import Path
from types import SimpleNamespace

import pytest

from database import create_session_factory
from models import Form, Logo, SiteFooter, User
from services.footer_logo_service import build_site_footer_view, resolve_footer_logo_url
from test_admin_panel import admin_app, admin_client  # noqa: F401
from werkzeug.security import generate_password_hash


def create_form(app, *, slug: str, name: str) -> int:
    with create_session_factory(app.config["DATABASE_URL"])() as db:
        form = Form(slug=slug, name=name, title=name, definition_json={"title": name, "fields": []})
        db.add(form)
        db.commit()
        return form.id


def create_user(app) -> int:
    with create_session_factory(app.config["DATABASE_URL"])() as db:
        user = User(
            email="admin@example.com",
            password_hash=generate_password_hash("secret"),
            role="super_admin",
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
        db.add(SiteFooter(name="Matching", html_body="<p>Ta sama treść</p>", logo_id=logo.id, logo_position="bottom", is_active=True))
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
