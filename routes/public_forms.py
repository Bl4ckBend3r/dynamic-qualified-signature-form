from __future__ import annotations

import logging

from flask import Blueprint, abort, current_app, flash, render_template, request

from services.nextcloud_storage import NextcloudStorageError

logger = logging.getLogger(__name__)

bp = Blueprint("public_forms", __name__)


def get_services():
    return current_app.extensions["services"]


@bp.get("/")
def index():
    services = get_services()
    try:
        services.storage.ensure_base_structure()
        services.storage.ensure_outputs_for_all_forms()
        forms = services.form_config_service.list_forms(services.storage)
        return render_template("index.html", forms=forms)
    except NextcloudStorageError as exc:
        logger.exception("Błąd Nextcloud: %s", exc)
        return f"Błąd Nextcloud: {exc}", 500


@bp.get("/form/<slug>")
def form_page(slug: str):
    services = get_services()
    form_meta = services.form_config_service.get_form_meta(services.storage, slug)
    if not form_meta:
        abort(404)

    form_config = services.form_config_service.get_form_config(services.storage, slug)
    if not form_config:
        abort(404)

    return render_template(
        "form_page.html",
        slug=slug,
        form_meta=form_meta,
        form_definition=form_config,
        errors={},
        values={},
    )


@bp.post("/submit/<slug>")
def submit(slug: str):
    services = get_services()
    form_meta = services.form_config_service.get_form_meta(services.storage, slug)
    if not form_meta:
        abort(404)

    form_config = services.form_config_service.get_form_config(services.storage, slug)
    if not form_config:
        abort(404)

    try:
        submission_result = services.submission_service.submit_form(slug, form_config, request.form)
        if not submission_result["ok"]:
            flash("Formularz zawiera błędy. Popraw wskazane pola.", "error")
            return render_template(
                "form_page.html",
                slug=slug,
                form_meta=form_meta,
                form_definition=form_config,
                errors=submission_result["errors"],
                values=submission_result["values"],
            ), 400

        return render_template("result.html", result=submission_result["result"])

    except Exception as exc:
        logger.exception("Błąd przetwarzania formularza: %s", exc)
        flash("Wystąpił błąd podczas przetwarzania formularza.", "error")
        return render_template(
            "form_page.html",
            slug=slug,
            form_meta=form_meta,
            form_definition=form_config,
            errors={},
            values=request.form,
        ), 500
