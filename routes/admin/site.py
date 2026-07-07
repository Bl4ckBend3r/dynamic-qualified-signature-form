from __future__ import annotations

from pathlib import Path

from flask import abort, current_app, flash, g, redirect, render_template, request, send_file, url_for
from sqlalchemy import select

from models import ContactPage, ServiceDocument
from services.site_document_service import (
    SERVICE_DOCUMENT_TYPES,
    save_document_upload,
    update_service_document_from_upload,
)
from services.upload_validation import UploadValidationError

from . import ROLE_SUPER_ADMIN, bp, db_session_factory, login_required


@bp.route("/site/contact", methods=["GET", "POST"])
@login_required
def contact_page_edit():
    if g.admin_user.role != ROLE_SUPER_ADMIN:
        return "Nie masz uprawnień do edycji tej strony.", 403
    with db_session_factory()() as db:
        page = db.execute(select(ContactPage).order_by(ContactPage.id)).scalar_one_or_none()
        if not page:
            page = ContactPage(title="Kontakt")
            db.add(page)
            db.flush()
        if request.method == "POST":
            page.title = request.form.get("title", "").strip() or "Kontakt"
            page.content_html = request.form.get("content_html", "").strip()
            page.contact_details = request.form.get("contact_details", "").strip()
            page.email = request.form.get("email", "").strip()
            page.phone = request.form.get("phone", "").strip()
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
