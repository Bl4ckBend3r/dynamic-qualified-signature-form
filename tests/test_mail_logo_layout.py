from types import SimpleNamespace

import pytest

from services.mail_template_service import render_platform_mail_html


def render_logo(position: str, *, alignment: str = "center", height: int = 64, logo_url: str = "cid:mail-logo") -> str:
    template = SimpleNamespace(
        name="Receipt",
        content_title="Receipt",
        html_body='<p id="mail-body">BODY-CONTENT</p>',
        text_body="BODY-CONTENT",
        instruction_html="",
        instruction_text="",
        footer_note="",
    )
    return render_platform_mail_html(
        template,
        {"form_name": "Form", "submission_id": "abc", "process_status": "FORM_SUBMITTED"},
        layout={
            "platform_name": "Platform name",
            "logo_url": logo_url,
            "logo_position": position,
            "logo_alignment": alignment,
            "logo_height_px": height,
            "footer_html": '<p id="layout-footer">FOOTER-CONTENT</p>',
        },
    )


@pytest.mark.parametrize(
    ("position", "before_marker", "after_marker"),
    [
        ("header", 'data-logo-position="header"', '<tr><td class="platform-title"'),
        ("before_content", 'data-logo-position="before_content"', "BODY-CONTENT"),
        ("after_content", "BODY-CONTENT", 'data-logo-position="after_content"'),
        ("footer", "BODY-CONTENT", 'data-logo-position="footer"'),
    ],
)
def test_logo_is_rendered_once_in_selected_position(position, before_marker, after_marker):
    html = render_logo(position)

    assert html.count('src="cid:mail-logo"') == 1
    assert html.count("platform-logo platform-logo-") == 1
    assert html.index(before_marker) < html.index(after_marker)


def test_footer_logo_is_grouped_with_html_footer_and_keeps_footer_content():
    html = render_logo("footer", alignment="right", height=88)

    footer = html.split('class="platform-layout-footer"', 1)[1]
    assert 'data-logo-position="footer"' in footer
    assert "FOOTER-CONTENT" in footer
    assert 'height="88"' in footer
    assert "height:88px;width:auto;max-width:100%;display:block;border:0" in footer
    assert "text-align:right" in footer
    assert footer.index('data-logo-position="footer"') < footer.index("FOOTER-CONTENT")


def test_none_or_unusable_logo_source_renders_no_image_or_empty_logo_container():
    disabled = render_logo("none")
    local_path = render_logo("footer", logo_url=r"C:\private\logo.png")
    missing = render_logo("footer", logo_url="")

    for html in (disabled, local_path, missing):
        assert "<img" not in html
        assert "platform-logo platform-logo-" not in html
        assert "BODY-CONTENT" in html
        assert "FOOTER-CONTENT" in html


def test_logo_height_and_alignment_are_safely_normalized():
    too_large = render_logo("header", alignment="unexpected", height=999)
    too_small = render_logo("header", height=1)

    assert 'height="200"' in too_large
    assert "text-align:center" in too_large
    assert 'height="16"' in too_small
