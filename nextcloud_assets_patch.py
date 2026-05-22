from __future__ import annotations

import mimetypes
import sys
from io import BytesIO
from pathlib import Path
from typing import Any

from flask import Flask, abort, send_file

_original_flask_init = Flask.__init__


def _app_module() -> Any:
    return sys.modules.get("app") or sys.modules.get("__main__")


def _normalize_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().strip("/")


def _resolve_nextcloud_asset_path(asset_path: str) -> str:
    app_module = _app_module()
    normalized = _normalize_path(asset_path)

    if app_module is None:
        return normalized

    forms_dir = _normalize_path(app_module.app.config.get("NEXTCLOUD_FORMS_DIR", "Formularze")) or "Formularze"
    output_dir = _normalize_path(app_module.app.config.get("NEXTCLOUD_OUTPUT_DIR", "output")) or "output"

    if normalized.startswith((forms_dir + "/", output_dir + "/")):
        return normalized

    return f"{forms_dir}/{normalized}"


def _register_nextcloud_asset_route(app: Flask) -> None:
    if "nextcloud_asset" in app.view_functions:
        return

    @app.get("/nextcloud-assets/<path:asset_path>", endpoint="nextcloud_asset")
    def nextcloud_asset(asset_path: str):
        app_module = _app_module()

        if app_module is None or not hasattr(app_module, "storage"):
            abort(404)

        resolved_path = _resolve_nextcloud_asset_path(asset_path)

        try:
            file_bytes = app_module.storage.read_bytes(resolved_path)
        except Exception:
            abort(404)

        mime_type, _ = mimetypes.guess_type(Path(resolved_path).name)
        if not mime_type:
            mime_type = "application/octet-stream"

        return send_file(
            BytesIO(file_bytes),
            mimetype=mime_type,
            as_attachment=False,
            download_name=Path(resolved_path).name,
        )


def _patched_flask_init(self: Flask, *args, **kwargs):
    _original_flask_init(self, *args, **kwargs)
    _register_nextcloud_asset_route(self)


if not getattr(Flask, "_nextcloud_assets_patch_applied", False):
    Flask.__init__ = _patched_flask_init
    Flask._nextcloud_assets_patch_applied = True
