from __future__ import annotations

from pathlib import Path

from flask import abort, current_app, flash, g, redirect, render_template, request, send_file, url_for
from sqlalchemy import select

from models import ContactPage, ServiceDocument, SiteFooter
from services.footer_logo_service import build_site_footer_view
from services.instruction_html_service import sanitize_instruction_html
from services.contact_page_service import default_contact_page, ensure_contact_defaults, phones_from_form
from services.site_document_service import (
    SERVICE_DOCUMENT_TYPES,
    save_document_upload,
    update_service_document_from_upload,
)
from services.upload_validation import UploadValidationError

from . import (
    ROLE_SUPER_ADMIN,
    bp,
    can_select_active_logo,
    db_session_factory,
    list_active_logos,
    login_required,
    parse_optional_int,
)


@bp.route("/site/footer", methods=["GET", "POST"])
@login_required
def site_footer_edit():
    if g.admin_user.role != ROLE_SUPER_ADMIN:
        return "Nie masz uprawnień do edycji stopki strony.", 403
    with db_session_factory()() as db:
        footer = db.execute(select(SiteFooter).order_by(SiteFooter.id)).scalars().first()
        footer = footer or SiteFooter(name="Stopka strony", html_body="", is_active=True)
        logos = list_active_logos(db)
        if request.method == "POST":
            try:
                _update_site_footer_from_form(footer)
            except ValueError as exc:
                flash(str(exc), "error")
                return _render_site_footer_editor(footer, logos), 400
            selected_logo_id = parse_optional_int(request.form.get("logo_id"))
            if selected_logo_id and not can_select_active_logo(db, selected_logo_id):
                abort(403)
            footer.logo_id = selected_logo_id
            footer.logo_path = ""
            db.add(footer)
            db.commit()
            flash("Stopka strony została zapisana.", "success")
            return redirect(url_for("admin.site_footer_edit"))
        return _render_site_footer_editor(footer, logos)


def _render_site_footer_editor(footer: SiteFooter, logos: list) -> str:
    preview = build_site_footer_view(
        footer,
        logo_url_builder=lambda logo: url_for(
            "public_forms.logo_asset",
            logo_id=logo.id,
            filename=Path(logo.filename).name,
        ),
    )
    return render_template("admin/site/footer.html", footer=footer, logos=logos, footer_preview=preview)


def _update_site_footer_from_form(footer: SiteFooter) -> None:
    alignment = str(request.form.get("logo_alignment") or "left").strip()
    if alignment not in {"left", "center", "right"}:
        raise ValueError("Wybierz prawidłowe wyrównanie logo.")
    position = str(request.form.get("logo_position") or "top").strip()
    if position not in {"top", "bottom", "left", "right", "inline"}:
        raise ValueError("Wybierz prawidłowe położenie logo.")
    footer.name = request.form.get("name", "").strip() or "Stopka strony"
    footer.html_body = sanitize_instruction_html(request.form.get("html_body", ""))
    footer.logo_alignment = alignment
    footer.logo_position = position
    footer.logo_width = _site_footer_dimension(request.form.get("logo_width"), 20, 800, "Szerokość logo")
    footer.logo_height = _site_footer_dimension(request.form.get("logo_height"), 20, 400, "Wysokość logo")
    footer.is_active = request.form.get("is_active") == "on"


def _site_footer_dimension(value: str | None, minimum: int, maximum: int, label: str) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise ValueError(f"{label} musi być liczbą całkowitą od {minimum} do {maximum} px.") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{label} musi mieć wartość od {minimum} do {maximum} px.")
    return parsed


@bp.route("/site/contact", methods=["GET", "POST"])
@login_required
def contact_page_edit():
    if g.admin_user.role != ROLE_SUPER_ADMIN:
        return "Nie masz uprawnień do edycji tej strony.", 403
    with db_session_factory()() as db:
        page = db.execute(select(ContactPage).order_by(ContactPage.id)).scalar_one_or_none()
        if not page:
            page = default_contact_page()
            db.add(page)
            db.flush()
        ensure_contact_defaults(page)
        if request.method == "POST":
            page.title = request.form.get("title", "").strip() or "Kontakt"
            page.content_html = request.form.get("content_html", "").strip()
            page.address = request.form.get("address", "").strip()
            page.email = request.form.get("email", "").strip()
            page.phones = phones_from_form(request.form.getlist("phone_label"), request.form.getlist("phone_number"))
            page.phone = page.phones[0]["number"] if page.phones else ""
            page.updated_by_user_id = g.admin_user.id
            db.commit()
            flash("Strona kontaktowa została zaktualizowana.", "success")
            return redirect(url_for("admin.contact_page_edit"))
        return render_template("admin/site/contact.html", page=page)


@bp.route("/site/documents", methods=["GET", "POST"])
@login_required
def service_documents_edit():
    if g.admin_user.role != ROLE_SUPER_ADMIN:
        return "Nie masz uprawnień.", 403
    with db_session_factory()() as db:
        if request.method == "POST":
            document_type = request.form.get("document_type", "").strip()
            if document_type not in SERVICE_DOCUMENT_TYPES:
                abort(400)
            document = db.execute(select(ServiceDocument).where(ServiceDocument.document_type == document_type)).scalar_one_or_none()
            if not document:
                document = ServiceDocument(document_type=document_type, title=SERVICE_DOCUMENT_TYPES[document_type])
                db.add(document)
            document.title = request.form.get("title", "").strip() or SERVICE_DOCUMENT_TYPES[document_type]
            document.content_html = request.form.get("content_html", "").strip()
            uploaded_file = request.files.get("document_file")
            if uploaded_file and uploaded_file.filename:
                uploaded_bytes = uploaded_file.read()
                try:
                    metadata = save_document_upload(
                        temp_dir=current_app.config["TEMP_DIR"],
                        uploaded_filename=uploaded_file.filename,
                        uploaded_bytes=uploaded_bytes,
                        uploaded_mimetype=uploaded_file.mimetype,
                    )
                except UploadValidationError as exc:
                    flash(str(exc), "error")
                    return redirect(url_for("admin.service_documents_edit"))
                update_service_document_from_upload(document, metadata, uploaded_by_user_id=g.admin_user.id)
            else:
                document.updated_by_user_id = g.admin_user.id
            db.commit()
            flash("Dokument serwisu został zapisany.", "success")
            return redirect(url_for("admin.service_documents_edit"))

        documents = {
            item.document_type: item
            for item in db.execute(select(ServiceDocument).order_by(ServiceDocument.document_type)).scalars().all()
        }
        return render_template(
            "admin/site/documents.html",
            document_types=SERVICE_DOCUMENT_TYPES,
            documents=documents,
        )


@bp.get("/site/documents/<int:document_id>/file")
@login_required
def service_document_admin_file(document_id: int):
    if g.admin_user.role != ROLE_SUPER_ADMIN:
        return "Nie masz uprawnień.", 403
    with db_session_factory()() as db:
        document = db.get(ServiceDocument, document_id) or abort(404)
        path = Path(document.storage_path or "")
        if not path.exists():
            abort(404)
        return send_file(path, mimetype=document.mime_type or None, download_name=document.original_filename)
