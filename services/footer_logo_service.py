from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any, Callable

from markupsafe import Markup

from services.instruction_html_service import sanitize_instruction_html


FOOTER_LOGO_POSITIONS = {"top", "bottom", "left", "right", "inline"}
FOOTER_LOGO_ALIGNMENTS = {"left", "center", "right"}
FOOTER_LOGO_PLACEHOLDER = "{{ footer_logo }}"


def normalize_footer_logo_position(value: object) -> str:
    position = str(value or "top").strip()
    return position if position in FOOTER_LOGO_POSITIONS else "top"


def normalize_footer_logo_alignment(value: object) -> str:
    alignment = str(value or "left").strip()
    return alignment if alignment in FOOTER_LOGO_ALIGNMENTS else "left"


def normalize_footer_logo_dimension(value: object, minimum: int, maximum: int) -> int | None:
    try:
        parsed = int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
    return parsed if parsed is not None and minimum <= parsed <= maximum else None


def resolve_footer_logo_url(
    footer: object,
    logo_url_builder: Callable[[Any], object] | None = None,
    *,
    allow_relative: bool = False,
) -> str:
    """Resolve a footer-owned logo in logo_id, logo_path, none order.

    A relationship supplied without ``logo_id`` is accepted for small adapters and
    tests, but an ORM footer with ``logo_id=None`` never consumes a stale relation.
    """
    logo = getattr(footer, "logo", None)
    logo_id_is_configured = getattr(footer, "logo_id", None) is not None
    relationship_adapter = not hasattr(footer, "logo_id")
    if (logo_id_is_configured or relationship_adapter) and footer_logo_is_usable(logo) and logo_url_builder:
        try:
            resolved = str(logo_url_builder(logo) or "").strip()
        except (TypeError, ValueError):
            resolved = ""
        if _allowed_logo_url(resolved, allow_relative=allow_relative):
            return resolved

    logo_path = str(getattr(footer, "logo_path", "") or "").strip()
    if _allowed_logo_url(logo_path, allow_relative=allow_relative):
        return logo_path
    return ""


def build_site_footer_view(footer: object | None, logo_url_builder=None) -> dict[str, object]:
    if footer is None or getattr(footer, "is_active", True) is False:
        return _empty_site_footer_view()

    position = normalize_footer_logo_position(getattr(footer, "logo_position", "top"))
    alignment = normalize_footer_logo_alignment(getattr(footer, "logo_alignment", "left"))
    width = normalize_footer_logo_dimension(getattr(footer, "logo_width", None), 20, 800)
    height = normalize_footer_logo_dimension(getattr(footer, "logo_height", None), 20, 400)
    logo_url = resolve_footer_logo_url(footer, logo_url_builder, allow_relative=True)
    logo = getattr(footer, "logo", None)
    logo_html = _site_logo_html(
        logo_url,
        alt=str(getattr(logo, "name", "") or "Logo stopki"),
        alignment=alignment,
        width=width,
        height=height,
        inline=False,
    )
    inline_logo_html = _site_logo_html(
        logo_url,
        alt=str(getattr(logo, "name", "") or "Logo stopki"),
        alignment=alignment,
        width=width,
        height=height,
        inline=True,
    )

    content_html = sanitize_instruction_html(getattr(footer, "html_body", "") or "")
    if position == "inline":
        if FOOTER_LOGO_PLACEHOLDER in content_html and inline_logo_html:
            content_html = content_html.replace(FOOTER_LOGO_PLACEHOLDER, inline_logo_html)
        else:
            content_html = content_html.replace(FOOTER_LOGO_PLACEHOLDER, "")
        logo_html = ""
    else:
        content_html = content_html.replace(FOOTER_LOGO_PLACEHOLDER, "")

    return {
        "configured": True,
        "content_html": Markup(content_html),
        "logo_html": Markup(logo_html),
        "logo_url": logo_url,
        "logo_position": position,
        "logo_alignment": alignment,
        "logo_width": width,
        "logo_height": height,
    }


def _empty_site_footer_view() -> dict[str, object]:
    return {
        "configured": False,
        "content_html": Markup(""),
        "logo_html": Markup(""),
        "logo_url": "",
        "logo_position": "top",
        "logo_alignment": "left",
        "logo_width": None,
        "logo_height": None,
    }


def footer_logo_is_usable(logo: object | None) -> bool:
    if not logo or getattr(logo, "active", False) is False:
        return False
    storage_path = getattr(logo, "storage_path", None)
    if storage_path not in (None, "") and not Path(str(storage_path)).is_file():
        return False
    mime_type = str(getattr(logo, "mime_type", "") or "").strip().lower()
    return not mime_type or mime_type.startswith("image/")


def _allowed_logo_url(value: str, *, allow_relative: bool) -> bool:
    lowered = value.lower()
    return lowered.startswith(("https://", "http://", "cid:")) or (
        allow_relative and value.startswith("/") and not value.startswith("//")
    )


def _site_logo_html(
    logo_url: str,
    *,
    alt: str,
    alignment: str,
    width: int | None,
    height: int | None,
    inline: bool,
) -> str:
    if not logo_url:
        return ""
    image_width = width if width is not None else (None if height is not None else 160)
    styles = ["display:inline-block", "max-width:100%", "object-fit:contain"]
    styles.append(f"width:{image_width}px" if image_width is not None else "width:auto")
    styles.append(f"height:{height}px" if height is not None else "height:auto")
    image = (
        f'<img class="site-footer__logo-image" src="{escape(logo_url, quote=True)}" '
        f'alt="{escape(alt, quote=True)}" style="{";".join(styles)}">'
    )
    tag = "span" if inline else "div"
    inline_style = "display:inline-block;vertical-align:middle;" if inline else ""
    return (
        f'<{tag} class="site-footer__logo" data-footer-logo '
        f'style="{inline_style}text-align:{alignment};">{image}</{tag}>'
    )
