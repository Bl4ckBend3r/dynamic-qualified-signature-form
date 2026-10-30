from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from markupsafe import Markup

from services.instruction_html_service import sanitize_instruction_html


FOOTER_LOGO_POSITIONS = {"top", "bottom", "left", "right", "inline"}
FOOTER_LOGO_ALIGNMENTS = {"left", "center", "right"}
FOOTER_LAYOUTS = {"single", "two_columns", "left_logo_right_content", "left_content_right_logo"}
FOOTER_SOCIAL_POSITIONS = {"left", "right", "bottom", "inline"}
FOOTER_SOCIAL_ICON_STYLES = {"gold", "light", "dark", "brand"}
FOOTER_SOCIAL_PLATFORMS = {"facebook", "youtube", "instagram", "linkedin", "x", "www"}
FOOTER_LOGO_PLACEHOLDER = "{{ footer_logo }}"
FOOTER_SOCIAL_PLACEHOLDER = "{{ footer_social_links }}"

_SOCIAL_LABELS = {
    "facebook": "Facebook",
    "youtube": "YouTube",
    "instagram": "Instagram",
    "linkedin": "LinkedIn",
    "x": "X",
    "www": "Strona WWW",
}
_SOCIAL_SVGS = {
    "facebook": '<path d="M14 8h3V4h-3c-3.3 0-5 2-5 5v2H6v4h3v9h4v-9h3.5l.5-4h-4V9c0-.7.3-1 1-1Z"/>',
    "youtube": '<path d="M23.5 7.2a3 3 0 0 0-2.1-2.1C19.5 4.5 12 4.5 12 4.5s-7.5 0-9.4.6A3 3 0 0 0 .5 7.2 31 31 0 0 0 0 12a31 31 0 0 0 .5 4.8 3 3 0 0 0 2.1 2.1c1.9.6 9.4.6 9.4.6s7.5 0 9.4-.6a3 3 0 0 0 2.1-2.1A31 31 0 0 0 24 12a31 31 0 0 0-.5-4.8ZM9.6 15.7V8.3L16 12l-6.4 3.7Z"/>',
    "instagram": '<path d="M7 2h10a5 5 0 0 1 5 5v10a5 5 0 0 1-5 5H7a5 5 0 0 1-5-5V7a5 5 0 0 1 5-5Zm0 2a3 3 0 0 0-3 3v10a3 3 0 0 0 3 3h10a3 3 0 0 0 3-3V7a3 3 0 0 0-3-3H7Zm11.5 1.5a1.25 1.25 0 1 1 0 2.5 1.25 1.25 0 0 1 0-2.5ZM12 7a5 5 0 1 1 0 10 5 5 0 0 1 0-10Zm0 2a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z"/>',
    "linkedin": '<path d="M5.3 7.8H1.2V21h4.1V7.8ZM3.3 1.2a2.4 2.4 0 1 0 0 4.8 2.4 2.4 0 0 0 0-4.8ZM21.8 13.4c0-4-2.1-5.9-5-5.9a4.3 4.3 0 0 0-3.9 2.1V7.8H8.8V21h4.1v-6.5c0-1.7.3-3.4 2.5-3.4s2.2 2 2.2 3.5V21h4.2v-7.6Z"/>',
    "x": '<path d="M18.9 2H22l-6.8 7.8L23.2 22H17l-4.8-6.3L6.7 22H3.5l7.2-8.2L3 2h6.3l4.4 5.8L18.9 2Zm-1.1 17.8h1.7L8.4 4.1H6.6l11.2 15.7Z"/>',
    "www": '<path fill="none" stroke="currentColor" stroke-width="2" d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm0 0c3 2.7 4.5 6 4.5 10S15 19.3 12 22M12 2C9 4.7 7.5 8 7.5 12S9 19.3 12 22M2 12h20"/>',
}


def _normalized(value: object, allowed: set[str], default: str) -> str:
    result = str(value or default).strip()
    return result if result in allowed else default


def normalize_footer_logo_position(value: object) -> str:
    return _normalized(value, FOOTER_LOGO_POSITIONS, "top")


def normalize_footer_logo_alignment(value: object) -> str:
    return _normalized(value, FOOTER_LOGO_ALIGNMENTS, "left")


def normalize_footer_logo_dimension(value: object, minimum: int, maximum: int) -> int | None:
    try:
        parsed = int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
    return parsed if parsed is not None and minimum <= parsed <= maximum else None


def normalize_social_links(value: object, *, strict: bool = False) -> list[dict[str, object]]:
    if not isinstance(value, list):
        if strict and value not in (None, ""):
            raise ValueError("Lista odnośników społecznościowych ma nieprawidłowy format.")
        return []
    normalized = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            if strict:
                raise ValueError(f"Wiersz {index} odnośników ma nieprawidłowy format.")
            continue
        platform = str(item.get("platform") or "").strip().lower()
        url = str(item.get("url") or "").strip()
        label = str(item.get("label") or "").strip()
        active_value = item.get("is_active", item.get("active"))
        active = active_value is True or str(active_value or "").lower() in {"1", "true", "on", "yes"}
        if not active and not platform and not url and not label:
            continue
        if platform not in FOOTER_SOCIAL_PLATFORMS:
            if strict:
                raise ValueError(f"Wybierz prawidłową platformę w wierszu {index}.")
            continue
        if active and not url:
            if strict:
                raise ValueError(f"Podaj adres URL aktywnego odnośnika w wierszu {index}.")
            continue
        if url and not _is_http_url(url):
            if strict:
                raise ValueError(f"Adres URL w wierszu {index} musi zaczynać się od http:// lub https://.")
            continue
        try:
            sort_order = int(item.get("sort_order", index))
        except (TypeError, ValueError):
            if strict:
                raise ValueError(f"Kolejność w wierszu {index} musi być liczbą całkowitą.")
            sort_order = index
        normalized.append({
            "platform": platform,
            "url": url,
            "label": label or _SOCIAL_LABELS[platform],
            "is_active": active,
            "sort_order": sort_order,
        })
    return sorted(normalized, key=lambda item: (int(item["sort_order"]), str(item["platform"])))


def resolve_footer_logo_url(footer: object, logo_url_builder: Callable[[Any], object] | None = None, *, allow_relative: bool = False) -> str:
    """Resolve a footer-owned logo in logo_id, logo_path, none order."""
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
    return logo_path if _allowed_logo_url(logo_path, allow_relative=allow_relative) else ""


def build_site_footer_view(footer: object | None, logo_url_builder=None) -> dict[str, object]:
    if footer is None or getattr(footer, "is_active", True) is False:
        return _empty_site_footer_view()

    layout = _normalized(getattr(footer, "layout", "two_columns"), FOOTER_LAYOUTS, "two_columns")
    position = normalize_footer_logo_position(getattr(footer, "logo_position", "top"))
    alignment = normalize_footer_logo_alignment(getattr(footer, "logo_alignment", "left"))
    social_position = _normalized(getattr(footer, "social_position", "left"), FOOTER_SOCIAL_POSITIONS, "left")
    social_style = _normalized(getattr(footer, "social_icon_style", "gold"), FOOTER_SOCIAL_ICON_STYLES, "gold")
    width = normalize_footer_logo_dimension(getattr(footer, "logo_width", None), 20, 800)
    height = normalize_footer_logo_dimension(getattr(footer, "logo_height", None), 20, 400)
    logo_url = resolve_footer_logo_url(footer, logo_url_builder, allow_relative=True)
    logo = getattr(footer, "logo", None)
    logo_html = _site_logo_html(logo_url, alt=str(getattr(logo, "name", "") or "Logo stopki"), alignment=alignment, width=width, height=height, inline=False)
    inline_logo = _site_logo_html(logo_url, alt=str(getattr(logo, "name", "") or "Logo stopki"), alignment=alignment, width=width, height=height, inline=True)

    legacy = sanitize_instruction_html(getattr(footer, "html_body", "") or "")
    left = sanitize_instruction_html(getattr(footer, "left_html", "") or "")
    right = sanitize_instruction_html(getattr(footer, "right_html", "") or "")
    if layout == "single":
        left, right = left or legacy, ""
    elif layout == "two_columns":
        if not left and not right:
            left = legacy
    elif layout == "left_logo_right_content":
        left, right = "", right or legacy or left
    else:
        left, right = left or legacy or right, ""

    if position == "inline":
        left = left.replace(FOOTER_LOGO_PLACEHOLDER, inline_logo if inline_logo else "")
        right = right.replace(FOOTER_LOGO_PLACEHOLDER, inline_logo if inline_logo else "")
        logo_html = ""
    else:
        left = left.replace(FOOTER_LOGO_PLACEHOLDER, "")
        right = right.replace(FOOTER_LOGO_PLACEHOLDER, "")

    social_show_labels = bool(getattr(footer, "social_show_labels", False))
    social_html = _social_links_html(
        normalize_social_links(getattr(footer, "social_links", None)),
        social_style,
        show_labels=social_show_labels,
    )
    social_left = social_right = social_bottom = ""
    if social_position == "inline":
        placeholder_found = FOOTER_SOCIAL_PLACEHOLDER in left or FOOTER_SOCIAL_PLACEHOLDER in right
        left = left.replace(FOOTER_SOCIAL_PLACEHOLDER, social_html)
        right = right.replace(FOOTER_SOCIAL_PLACEHOLDER, social_html)
        if social_html and not placeholder_found:
            social_bottom = social_html
    else:
        left = left.replace(FOOTER_SOCIAL_PLACEHOLDER, "")
        right = right.replace(FOOTER_SOCIAL_PLACEHOLDER, "")
        if social_position == "left":
            social_left = social_html
        elif social_position == "right":
            social_right = social_html
        else:
            social_bottom = social_html

    return {
        "configured": True,
        "layout": layout,
        "left_html": Markup(left),
        "right_html": Markup(right),
        "content_html": Markup(left),  # compatibility for existing integrations/tests
        "logo_html": Markup(logo_html),
        "logo_url": logo_url,
        "logo_position": position,
        "logo_alignment": alignment,
        "logo_width": width,
        "logo_height": height,
        "social_position": social_position,
        "social_icon_style": social_style,
        "social_show_labels": social_show_labels,
        "social_left_html": Markup(social_left),
        "social_right_html": Markup(social_right),
        "social_bottom_html": Markup(social_bottom),
    }


def _empty_site_footer_view() -> dict[str, object]:
    return {
        "configured": False, "layout": "two_columns", "left_html": Markup(""), "right_html": Markup(""),
        "content_html": Markup(""), "logo_html": Markup(""), "logo_url": "", "logo_position": "top",
        "logo_alignment": "left", "logo_width": None, "logo_height": None, "social_position": "left",
        "social_icon_style": "gold", "social_show_labels": False, "social_left_html": Markup(""), "social_right_html": Markup(""),
        "social_bottom_html": Markup(""),
    }


def footer_logo_is_usable(logo: object | None) -> bool:
    if not logo or getattr(logo, "active", False) is False:
        return False
    storage_path = getattr(logo, "storage_path", None)
    if storage_path not in (None, "") and not Path(str(storage_path)).is_file():
        return False
    mime_type = str(getattr(logo, "mime_type", "") or "").strip().lower()
    return not mime_type or mime_type.startswith("image/")


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def _allowed_logo_url(value: str, *, allow_relative: bool) -> bool:
    lowered = value.lower()
    return lowered.startswith(("https://", "http://", "cid:")) or (allow_relative and value.startswith("/") and not value.startswith("//"))


def _social_links_html(links: list[dict[str, object]], style: str, *, show_labels: bool) -> str:
    rendered = []
    for link in links:
        if not link["is_active"]:
            continue
        platform = str(link["platform"])
        label = escape(str(link["label"]), quote=True)
        label_class = " site-footer__social-link--with-label" if show_labels else ""
        visible_label = f'<span class="site-footer__social-label-visible">{label}</span>' if show_labels else ""
        rendered.append(
            f'<a class="site-footer__social-link site-footer__social-link--{platform}{label_class}" href="{escape(str(link["url"]), quote=True)}" '
            f'target="_blank" rel="noopener noreferrer" aria-label="{label}">'
            f'<span class="site-footer__social-icon" aria-hidden="true">'
            f'<svg viewBox="0 0 24 24" focusable="false">{_SOCIAL_SVGS[platform]}</svg></span>'
            f'{visible_label}</a>'
        )
    if not rendered:
        return ""
    return f'<nav class="site-footer__social site-footer__social--{style}" aria-label="Media społecznościowe">{"".join(rendered)}</nav>'


def _site_logo_html(logo_url: str, *, alt: str, alignment: str, width: int | None, height: int | None, inline: bool) -> str:
    if not logo_url:
        return ""
    image_width = width if width is not None else (None if height is not None else 160)
    styles = ["display:inline-block", "max-width:100%", "object-fit:contain"]
    styles.append(f"width:{image_width}px" if image_width is not None else "width:auto")
    styles.append(f"height:{height}px" if height is not None else "height:auto")
    image = f'<img class="site-footer__logo-image" src="{escape(logo_url, quote=True)}" alt="{escape(alt, quote=True)}" style="{";".join(styles)}">'
    tag = "span" if inline else "div"
    inline_style = "display:inline-block;vertical-align:middle;" if inline else ""
    return f'<{tag} class="site-footer__logo" data-footer-logo style="{inline_style}text-align:{alignment};">{image}</{tag}>'
