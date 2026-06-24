from __future__ import annotations

from pathlib import Path

TEMPLATE_PATH = Path("templates/documents_to_sign.html")
LT = chr(60)
GT = chr(62)
QUOTE = chr(34)


def tag(name: str, closing: bool = False) -> str:
    return LT + ("/" if closing else "") + name + GT


def asset_blocks() -> str:
    css_line = "    " + LT + "link rel=" + QUOTE + "stylesheet" + QUOTE + " href=" + QUOTE + "{{ url_for('static', filename='documents_to_sign.css') }}" + QUOTE + GT
    js_line = "    " + LT + "script src=" + QUOTE + "{{ url_for('static', filename='documents_to_sign.js') }}" + QUOTE + GT + LT + "/script" + GT
    return "\n".join([
        "{% block extra_css %}",
        css_line,
        "{% endblock %}",
        "",
        "{% block extra_js %}",
        js_line,
        "{% endblock %}",
    ])


def remove_block(source: str, start_marker: str, end_marker: str) -> tuple[str, bool]:
    start = source.find(start_marker)
    if start == -1:
        return source, False
    end = source.find(end_marker, start)
    if end == -1:
        raise RuntimeError("Missing closing marker")
    end += len(end_marker)
    while end < len(source) and source[end] in " \t\r\n":
        end += 1
    return source[:start].rstrip() + "\n" + source[end:].lstrip(), True


def add_asset_blocks(source: str) -> tuple[str, bool]:
    if "documents_to_sign.css" in source and "documents_to_sign.js" in source:
        return source, False
    title_block = "{% block title %}Do podpisania{% endblock %}"
    title_index = source.find(title_block)
    if title_index == -1:
        raise RuntimeError("Cannot find title block")
    insert_at = title_index + len(title_block)
    return source[:insert_at] + "\n\n" + asset_blocks() + source[insert_at:], True


def patch_template() -> bool:
    source = TEMPLATE_PATH.read_text(encoding="utf-8")
    patched, changed_assets = add_asset_blocks(source)
    patched, removed_style = remove_block(patched, tag("style"), tag("style", closing=True))
    patched, removed_script = remove_block(patched, tag("script"), tag("script", closing=True))
    changed = changed_assets or removed_style or removed_script
    if changed:
        TEMPLATE_PATH.write_text(patched, encoding="utf-8")
    return changed


def main() -> None:
    changed = patch_template()
    print("P2.1 template patch applied." if changed else "P2.1 template patch already applied.")


if __name__ == "__main__":
    main()
