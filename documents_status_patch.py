from __future__ import annotations

from flask import Flask, request

_original_flask_init = Flask.__init__


def _patch_documents_status_html(html: str) -> str:
    if 'id="signing-result-box"' not in html:
        return html

    old = """if (submissionInput.value.trim()) {
    checkAcceptanceStatus();
} else {
    renderStatusTile({
        variant: "warning",
        icon: "i",
        title: "Podaj ID wniosku",
        description: "Status wniosku zostanie sprawdzony przed pokazaniem dokumentów do podpisu.",
    });
}"""

    new = """if (document.getElementById("signing-result-box")) {
    clearStatusTile();
    statusBox.textContent = "";
    disableGenerateButton();
} else if (submissionInput.value.trim()) {
    checkAcceptanceStatus();
} else {
    renderStatusTile({
        variant: "warning",
        icon: "i",
        title: "Podaj ID wniosku",
        description: "Status wniosku zostanie sprawdzony przed pokazaniem dokumentów do podpisu.",
    });
}"""

    return html.replace(old, new)


def _register_documents_status_patch(app: Flask) -> None:
    if getattr(app, "_documents_status_patch_registered", False):
        return

    @app.after_request
    def patch_documents_status_response(response):
        if request.endpoint != "documents_to_sign":
            return response

        content_type = response.headers.get("Content-Type", "")
        if "text/html" not in content_type.lower():
            return response

        html = response.get_data(as_text=True)
        patched_html = _patch_documents_status_html(html)

        if patched_html != html:
            response.set_data(patched_html)
            response.headers["Content-Length"] = str(len(response.get_data()))

        return response

    app._documents_status_patch_registered = True


def _patched_flask_init(self: Flask, *args, **kwargs):
    _original_flask_init(self, *args, **kwargs)
    _register_documents_status_patch(self)


if not getattr(Flask, "_documents_status_patch_applied", False):
    Flask.__init__ = _patched_flask_init
    Flask._documents_status_patch_applied = True
