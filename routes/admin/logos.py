from __future__ import annotations

from flask import abort, current_app, flash, g, redirect, render_template, request, send_file, url_for

from models import Form, Logo, MailFooter, SiteFooter, SystemMailSettings
from services.logo_service import (
    create_logo_from_upload,
    list_logos_for_admin,
    logo_asset_path_for_user,
    update_logo_metadata,
)
from services.upload_validation import UploadValidationError

from . import bp, db_session_factory, has_permission, login_required, permission_required


@bp.route("/logos", methods=["GET", "POST"])
@login_required
def logos_list():
    with db_session_factory()() as db:
        if request.method == "POST":
            if not has_permission(db, "can_manage_site"):
                abort(403)
            uploaded_file = request.files.get("logo_file")
            if not uploaded_file or not uploaded_file.filename:
                flash("Wybierz plik logo.", "error")
                return redirect(url_for("admin.logos_list"))
            logo_bytes = uploaded_file.read()
            try:
                create_logo_from_upload(
                    db,
                    temp_dir=current_app.config["TEMP_DIR"],
                    uploaded_filename=uploaded_file.filename,
                    uploaded_bytes=logo_bytes,
                    uploaded_mimetype=uploaded_file.mimetype,
                    name=request.form.get("name", ""),
                    uploaded_by_user_id=g.admin_user.id,
                )
            except UploadValidationError as exc:
                flash(str(exc), "error")
                return redirect(url_for("admin.logos_list"))
            db.commit()
            flash("Logo zostalo dodane.", "success")
            return redirect(url_for("admin.logos_list"))

        logos = list_logos_for_admin(db, g.admin_user, manage_all=has_permission(db, "can_manage_site"))
        logo_usage = {
            logo.id: db.query(Form).filter(Form.logo_id == logo.id).count()
            for logo in logos
        }
        return render_template("admin/logos/list.html", logos=logos, logo_usage=logo_usage)


@bp.post("/logos/<int:logo_id>/toggle")
@login_required
@permission_required("can_manage_site")
def logo_toggle(logo_id: int):
    with db_session_factory()() as db:
        logo = db.get(Logo, logo_id) or abort(404)
        logo.active = not logo.active
        db.commit()
        is_active = logo.active
    flash("Logo zostalo aktywowane." if is_active else "Logo zostalo dezaktywowane.", "success")
    return redirect(url_for("admin.logos_list"))


@bp.route("/logos/<int:logo_id>/edit", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_site")
def logo_edit(logo_id: int):
    with db_session_factory()() as db:
        logo = db.get(Logo, logo_id)
        if not logo:
            abort(404)
        if request.method == "POST":
            update_logo_metadata(
                logo,
                name=request.form.get("name", ""),
                active=request.form.get("active") == "on",
            )
            db.commit()
            flash("Logo zostalo zapisane.", "success")
            return redirect(url_for("admin.logos_list"))
        return render_template("admin/logos/edit.html", logo=logo)


@bp.get("/logos/<int:logo_id>/asset")
@login_required
def logo_asset(logo_id: int):
    with db_session_factory()() as db:
        logo = db.get(Logo, logo_id)
        logo_path = logo_asset_path_for_user(logo, g.admin_user, manage_all=has_permission(db, "can_manage_site"))
        if not logo_path:
            abort(404)
        return send_file(logo_path, mimetype=logo.mime_type or None)


@bp.post("/logos/<int:logo_id>/delete")
@login_required
@permission_required("can_manage_site")
def logo_delete(logo_id: int):
    with db_session_factory()() as db:
        logo = db.get(Logo, logo_id) or abort(404)
        forms_count = db.query(Form).filter(Form.logo_id == logo.id).count()
        footers_count = db.query(MailFooter).filter(MailFooter.logo_id == logo.id).count()
        site_footers_count = db.query(SiteFooter).filter(SiteFooter.logo_id == logo.id).count()
        if (forms_count or footers_count or site_footers_count) and request.form.get("detach") != "1":
            flash(
                f"Logo jest przypisane do {forms_count} formularzy, {footers_count} stopek mailowych "
                f"i {site_footers_count} stopek strony. Potwierdź bezpieczne odpięcie.",
                "warning",
            )
            return redirect(url_for("admin.logos_list"))
        db.query(Form).filter(Form.logo_id == logo.id).update({Form.logo_id: None}, synchronize_session=False)
        db.query(MailFooter).filter(MailFooter.logo_id == logo.id).update({MailFooter.logo_id: None}, synchronize_session=False)
        db.query(SiteFooter).filter(SiteFooter.logo_id == logo.id).update({SiteFooter.logo_id: None}, synchronize_session=False)
        for settings in db.query(SystemMailSettings).all():
            layout = dict(settings.layout_config or {})
            if layout.get("logo_id") == logo.id:
                layout["logo_id"] = None
                settings.layout_config = layout
        db.delete(logo)
        db.commit()
    flash(
        f"Logo zostało usunięte i odpięte od {forms_count} formularzy, "
        f"{footers_count} stopek mailowych i {site_footers_count} stopek strony.",
        "success",
    )
    return redirect(url_for("admin.logos_list"))
