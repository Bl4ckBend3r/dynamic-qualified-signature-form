import base64
import csv
import logging
import mimetypes
import tempfile
from io import BytesIO, StringIO
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from config import Config
from form_loader import (
    validate_submission,
    extract_submission_data,
    build_submission_view,
    build_consents_view,
)
from pdf_generator import generate_pdf
from signature_verifier import verify_signed_pdf
from services.nextcloud_storage import (
    NextcloudStorageError,
    create_nextcloud_storage_from_env,
)
from services.email_service import send_submission_decision_email
from services.process_service import (
    OfficerDecision,
    build_initial_process_fields,
    build_legacy_process_fields,
    build_process_state,
    get_officer_decision,
    should_send_officer_decision_email,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config.from_object(Config)

Path(app.config["TEMP_DIR"]).mkdir(parents=True, exist_ok=True)

storage = create_nextcloud_storage_from_env()

logger.info("NEXTCLOUD_BASE_URL=%s", app.config["NEXTCLOUD_BASE_URL"])
logger.info("NEXTCLOUD_USERNAME=%s", app.config["NEXTCLOUD_USERNAME"])
logger.info("NEXTCLOUD_FORMS_DIR=%s", app.config["NEXTCLOUD_FORMS_DIR"])
logger.info("NEXTCLOUD_OUTPUT_DIR=%s", app.config["NEXTCLOUD_OUTPUT_DIR"])


def resolve_pdf_image_url(form_definition: dict) -> str | None:
    image_value = form_definition.get("header_image")
    if not image_value:
        return None

    normalized = str(image_value).replace("\\", "/").lstrip("/")

    if normalized.startswith("static/"):
        normalized = normalized[len("static/"):]

    return request.url_root.rstrip("/") + "/static/" + normalized


def slug_to_title(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").strip().title()


def detect_form_files() -> list[str]:
    return storage.list_form_files()


def build_forms_registry() -> list[dict]:
    forms = []

    for index, filename in enumerate(detect_form_files()):
        try:
            form_definition = storage.read_form_json(filename)
            slug = Path(filename).stem
            storage.ensure_form_output_structure(slug)

            forms.append(
                {
                    "slug": slug,
                    "title": form_definition.get("title") or slug_to_title(slug),
                    "description": form_definition.get("description", ""),
                    "definition_path": filename,
                    "tile_variant": (
                        "featured"
                        if index == 0
                        else "accent"
                        if index % 3 == 1
                        else "light"
                        if index % 3 == 2
                        else "default"
                    ),
                }
            )
        except Exception as exc:
            logger.warning("Nie udało się załadować formularza %s: %s", filename, exc)

    return forms


def get_forms() -> list[dict]:
    return build_forms_registry()


def get_form_meta(slug: str) -> dict | None:
    for form in get_forms():
        if form["slug"] == slug:
            return form
    return None


def get_form_definition(slug: str) -> dict | None:
    form_meta = get_form_meta(slug)
    if not form_meta:
        return None
    return storage.read_form_json(form_meta["definition_path"])


def resolve_pdf_image_data_uri(app: Flask, form_definition: dict) -> str | None:
    image_value = form_definition.get("header_image")
    if not image_value:
        return None

    image_path = Path(image_value)

    if not image_path.is_absolute():
        image_path = Path(app.root_folder) / "static" / image_value

    if not image_path.exists():
        logger.warning("Nie znaleziono obrazu do PDF: %s", image_path)
        return None

    mime_type, _ = mimetypes.guess_type(image_path.name)
    if not mime_type:
        mime_type = "image/png"

    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def build_pdf_filename(slug: str, submission_id: str) -> str:
    return f"{slug}-{submission_id}.pdf"


def build_signed_pdf_filename(slug: str, submission_id: str) -> str:
    return f"{slug}-{submission_id}-signed.pdf"


def pdf_exists(slug: str, filename: str) -> bool:
    return storage.exists(f"{app.config['NEXTCLOUD_OUTPUT_DIR']}/{slug}/pdf/{filename}")


def read_submission_rows(slug: str) -> list[dict]:
    csv_path = f"{app.config['NEXTCLOUD_OUTPUT_DIR']}/{slug}/{app.config['CSV_FILENAME']}"

    if not storage.exists(csv_path):
        logger.warning("CSV nie istnieje: %s", csv_path)
        return []

    csv_bytes = storage.get_file_bytes(csv_path)
    csv_text = csv_bytes.decode("utf-8-sig")

    reader = csv.DictReader(StringIO(csv_text))
    return list(reader)


def find_submission_acceptance_by_id(submission_id: str) -> dict | None:
    for form in get_forms():
        slug = form["slug"]
        rows = read_submission_rows(slug)

        for row in rows:
            if row.get("submission_id", "").strip() != submission_id:
                continue

            process_state = build_process_state(row)

            return {
                "submission_id": submission_id,
                "form_slug": slug,
                "form_title": row.get("form_name") or form["title"],
                "officer_decision": process_state.officer_decision.value,
                "process_status": process_state.status.value,
                "can_sign_documents": process_state.can_sign_documents,
                "row": row,
            }

    return None


def build_decision_email_content(submission: dict, accepted: bool) -> tuple[str, str]:
    template_name = (
        "emails/decision_accepted.html"
        if accepted
        else "emails/decision_rejected.html"
    )

    html_body = render_template(
        template_name,
        submission_id=submission["submission_id"],
        form_title=submission["form_title"],
    )

    if accepted:
        text_body = (
            f"Dzień dobry,\n\n"
            f"wniosek dotyczący formularza „{submission['form_title']}” został zaakceptowany.\n\n"
            f"ID wniosku: {submission['submission_id']}\n\n"
            f"Możesz przejść do podpisywania dokumentów w zakładce „Do podpisania”.\n\n"
            f"Pozdrawiamy\n"
        )
    else:
        text_body = (
            f"Dzień dobry,\n\n"
            f"wniosek dotyczący formularza „{submission['form_title']}” nie został zaakceptowany.\n\n"
            f"ID wniosku: {submission['submission_id']}\n\n"
            f"W razie pytań prosimy o kontakt z urzędem.\n\n"
            f"Pozdrawiamy\n"
        )

    return html_body, text_body


def maybe_send_decision_email(submission: dict) -> None:
    row = submission["row"]

    email = row.get("email", "").strip()
    if not email:
        logger.warning(
            "Brak adresu e-mail dla wniosku %s",
            submission["submission_id"],
        )
        return

    officer_decision = get_officer_decision(row)

    if officer_decision == OfficerDecision.MISSING:
        return

    if not should_send_officer_decision_email(row):
        return

    accepted = officer_decision == OfficerDecision.ACCEPTED
    decision_value = officer_decision.value
    html_body, text_body = build_decision_email_content(submission, accepted)

    send_submission_decision_email(
        smtp_host=app.config["SMTP_HOST"],
        smtp_port=app.config["SMTP_PORT"],
        smtp_user=app.config["SMTP_USER"],
        smtp_password=app.config["SMTP_PASSWORD"],
        mail_from=app.config["MAIL_FROM"],
        to_email=email,
        submission_id=submission["submission_id"],
        form_title=submission["form_title"],
        accepted=accepted,
        html_body=html_body,
        text_body=text_body,
    )

    storage.update_csv_row_by_submission_id(
        submission["form_slug"],
        submission["submission_id"],
        {
            "officer_decision_email_sent": "Tak",
            "decision_email_sent": "Tak",
            "decision_email_sent_for": decision_value,
        },
    )

    logger.info(
        "Wysłano e-mail decyzji '%s' na adres %s dla wniosku %s",
        decision_value,
        email,
        submission["submission_id"],
    )


@app.context_processor
def inject_globals():
    return {
        "app_name": app.config["APP_NAME"],
    }


@app.route("/", methods=["GET"])
def index():
    try:
        storage.ensure_base_structure()
        storage.ensure_outputs_for_all_forms()
        forms = get_forms()
        return render_template("index.html", forms=forms)
    except NextcloudStorageError as exc:
        logger.exception("Błąd Nextcloud: %s", exc)
        return f"Błąd Nextcloud: {exc}", 500


@app.route("/form/<slug>", methods=["GET"])
def form_page(slug: str):
    form_meta = get_form_meta(slug)
    if not form_meta:
        abort(404)

    form_definition = get_form_definition(slug)
    if not form_definition:
        abort(404)

    return render_template(
        "form_page.html",
        slug=slug,
        form_meta=form_meta,
        form_definition=form_definition,
        errors={},
        values={},
    )


@app.route("/submit/<slug>", methods=["POST"])
def submit(slug: str):
    form_meta = get_form_meta(slug)
    if not form_meta:
        abort(404)

    form_definition = get_form_definition(slug)
    if not form_definition:
        abort(404)

    submission_id = str(uuid4())
    submission_data = extract_submission_data(form_definition, request.form)
    submission_data.update(build_initial_process_fields())
    submission_data.update(build_legacy_process_fields())

    errors = validate_submission(form_definition, submission_data)
    logger.info("Validation errors: %s", errors)

    if errors:
        flash("Formularz zawiera błędy. Popraw wskazane pola.", "error")
        return render_template(
            "form_page.html",
            slug=slug,
            form_meta=form_meta,
            form_definition=form_definition,
            errors=errors,
            values=submission_data,
        ), 400

    try:
        storage.ensure_form_output_structure(slug)

        pdf_filename = build_pdf_filename(slug, submission_id)

        pdf_context = {
            "form_definition": form_definition,
            "submission_view": build_submission_view(form_definition, submission_data),
            "submission_id": submission_id,
            "pdf_image_url": resolve_pdf_image_url(form_definition),
            "pdf_image_alt": form_definition.get("title", ""),
            "consents_view": build_consents_view(form_definition, submission_data),
        }

        logger.info("header_image: %s", form_definition.get("header_image"))
        logger.info("pdf_image_url: %s", pdf_context.get("pdf_image_url"))

        with tempfile.NamedTemporaryFile(
            suffix=".pdf",
            delete=False,
            dir=app.config["TEMP_DIR"],
        ) as tmp_pdf:
            tmp_pdf_path = Path(tmp_pdf.name)

        try:
            generate_pdf(
                app=app,
                template_name="pdf_template.html",
                context=pdf_context,
                output_path=tmp_pdf_path,
            )
            pdf_bytes = tmp_pdf_path.read_bytes()
            storage.save_pdf(slug, pdf_filename, pdf_bytes)
            logger.info("Wygenerowano i zapisano PDF do Nextcloud: %s", pdf_filename)
        finally:
            tmp_pdf_path.unlink(missing_ok=True)

        signature_status = "manual"
        signed_pdf_filename = ""

        csv_row = {
            "submission_id": submission_id,
            "form_slug": slug,
            "created_at": "",
            "form_name": form_definition["title"],
            "pdf_filename": pdf_filename,
            "signed_pdf_filename": signed_pdf_filename,
            "signature_status": signature_status,
            "signature_request_id": "mobywatel-manual",
            **submission_data,
        }

        storage.append_csv_row(slug, csv_row)

        result = {
            "submission_id": submission_id,
            "form_slug": slug,
            "pdf_filename": pdf_filename,
            "pdf_url": url_for("download_pdf", slug=slug, filename=pdf_filename),
            "signature_request_id": "mobywatel-manual",
            "signature_status": signature_status,
            "signed_pdf_filename": signed_pdf_filename,
            "signed_pdf_url": None,
            "upload_url": url_for("upload_signed_pdf", slug=slug, submission_id=submission_id),
            "form_title": form_definition["title"],
            "verification": None,
        }

        return render_template("result.html", result=result)

    except Exception as exc:
        logger.exception("Błąd przetwarzania formularza: %s", exc)
        flash("Wystąpił błąd podczas przetwarzania formularza.", "error")
        return render_template(
            "form_page.html",
            slug=slug,
            form_meta=form_meta,
            form_definition=form_definition,
            errors={},
            values=submission_data,
        ), 500


@app.route("/upload-signed/<slug>/<submission_id>", methods=["POST"])
def upload_signed_pdf(slug: str, submission_id: str):
    form_meta = get_form_meta(slug)
    if not form_meta:
        abort(404)

    form_definition = get_form_definition(slug)
    if not form_definition:
        abort(404)

    try:
        uploaded_file = request.files.get("signed_pdf")

        if not uploaded_file or not uploaded_file.filename:
            flash("Nie wybrano pliku PDF.", "error")
            return redirect(url_for("show_result", slug=slug, submission_id=submission_id))

        if not uploaded_file.filename.lower().endswith(".pdf"):
            flash("Dozwolony jest wyłącznie plik PDF.", "error")
            return redirect(url_for("show_result", slug=slug, submission_id=submission_id))

        signed_pdf_filename = build_signed_pdf_filename(slug, submission_id)
        uploaded_bytes = uploaded_file.read()

        with tempfile.NamedTemporaryFile(
            suffix=".pdf",
            delete=False,
            dir=app.config["TEMP_DIR"],
        ) as tmp_signed:
            tmp_signed_path = Path(tmp_signed.name)
            tmp_signed.write(uploaded_bytes)

        try:
            verification = verify_signed_pdf(tmp_signed_path)
        finally:
            tmp_signed_path.unlink(missing_ok=True)

        if not verification["is_signed"]:
            flash("Przesłany plik nie zawiera podpisu PDF.", "error")
            return redirect(url_for("show_result", slug=slug, submission_id=submission_id))

        if not verification["is_szafir_signature"]:
            flash("Przesłany plik nie jest podpisem Szafir / KIR.", "error")
            return redirect(url_for("show_result", slug=slug, submission_id=submission_id))

        storage.save_pdf(slug, signed_pdf_filename, uploaded_bytes)

        flash("Wykryto poprawny podpis Szafir / KIR.", "success")
        return redirect(url_for("show_result", slug=slug, submission_id=submission_id))

    except Exception as exc:
        logger.exception("Błąd uploadu podpisanego PDF: %s", exc)
        flash("Wystąpił błąd podczas wgrywania lub weryfikacji podpisu.", "error")
        return redirect(url_for("show_result", slug=slug, submission_id=submission_id))


@app.route("/result/<slug>/<submission_id>", methods=["GET"])
def show_result(slug: str, submission_id: str):
    form_meta = get_form_meta(slug)
    if not form_meta:
        abort(404)

    form_definition = get_form_definition(slug)
    if not form_definition:
        abort(404)

    pdf_filename = build_pdf_filename(slug, submission_id)
    signed_pdf_filename = build_signed_pdf_filename(slug, submission_id)

    verification = None
    signed_exists = pdf_exists(slug, signed_pdf_filename)

    if signed_exists:
        try:
            signed_pdf_bytes = storage.get_pdf_bytes(slug, signed_pdf_filename)
            with tempfile.NamedTemporaryFile(
                suffix=".pdf",
                delete=False,
                dir=app.config["TEMP_DIR"],
            ) as tmp_signed:
                tmp_signed_path = Path(tmp_signed.name)
                tmp_signed.write(signed_pdf_bytes)

            try:
                verification = verify_signed_pdf(tmp_signed_path)
            finally:
                tmp_signed_path.unlink(missing_ok=True)

        except Exception as exc:
            logger.warning("Nie udało się odczytać podpisu: %s", exc)

    result = {
        "submission_id": submission_id,
        "form_slug": slug,
        "pdf_filename": pdf_filename,
        "pdf_url": url_for("download_pdf", slug=slug, filename=pdf_filename),
        "signature_request_id": "mobywatel-manual",
        "signature_status": (
            "szafir"
            if verification and verification.get("is_szafir_signature")
            else "uploaded" if signed_exists else "manual"
        ),
        "signed_pdf_filename": signed_pdf_filename if signed_exists else "",
        "signed_pdf_url": (
            url_for("download_signed_pdf", slug=slug, filename=signed_pdf_filename)
            if signed_exists
            else None
        ),
        "upload_url": url_for("upload_signed_pdf", slug=slug, submission_id=submission_id),
        "form_title": form_definition["title"],
        "verification": verification,
    }

    return render_template("result.html", result=result)


@app.get("/api/submissions/<submission_id>/acceptance-status")
def api_acceptance_status(submission_id: str):
    submission_id = submission_id.strip()

    if not submission_id:
        return {
            "exists": False,
            "can_sign_documents": False,
            "message": "Nie podano ID wniosku.",
        }, 200

    try:
        submission = find_submission_acceptance_by_id(submission_id)
    except Exception as exc:
        logger.exception("Błąd sprawdzania akceptacji wniosku: %s", exc)
        return {
            "exists": False,
            "can_sign_documents": False,
            "message": "Nie udało się sprawdzić statusu wniosku.",
        }, 200

    if not submission:
        return {
            "exists": False,
            "can_sign_documents": False,
            "message": "Nie znaleziono wniosku o podanym ID.",
        }, 200

    try:
        maybe_send_decision_email(submission)
    except Exception as exc:
        logger.exception("Nie udało się wysłać e-maila decyzji: %s", exc)

    if submission["officer_decision"] == OfficerDecision.REJECTED.value:
        return {
            "exists": True,
            "can_sign_documents": False,
            "message": "Wniosek został odrzucony przez urzędnika.",
            "form_title": submission["form_title"],
            "process_status": submission["process_status"],
        }, 200

    if not submission["can_sign_documents"]:
        return {
            "exists": True,
            "can_sign_documents": False,
            "message": "Wniosek nie został jeszcze zaakceptowany przez urzędnika.",
            "form_title": submission["form_title"],
            "process_status": submission["process_status"],
        }, 200

    return {
        "exists": True,
        "can_sign_documents": True,
        "message": "Wniosek został zaakceptowany. Możesz przejść do podpisywania dokumentów.",
        "form_title": submission["form_title"],
        "form_slug": submission["form_slug"],
        "process_status": submission["process_status"],
    }, 200


@app.route("/do-podpisania", methods=["GET", "POST"])
def documents_to_sign():
    if request.method == "GET":
        return render_template(
            "documents_to_sign.html",
            submission_id="",
            acceptance_value="",
            errors={},
            result=None,
        )

    submission_id = request.form.get("submission_id", "").strip()
    acceptance_value = request.form.get("akceptacja", "").strip()

    errors = {}

    if not submission_id:
        errors["submission_id"] = "Podaj ID wniosku."
        submission = None
    else:
        submission = find_submission_acceptance_by_id(submission_id)

        if not submission:
            errors["submission_id"] = "Nie znaleziono wniosku o podanym ID."
        elif not submission["can_sign_documents"]:
            errors["submission_id"] = "Wniosek nie został jeszcze zaakceptowany przez urzędnika."

    if acceptance_value != "Tak":
        errors["akceptacja"] = "Akceptacja dokumentów jest wymagana."

    if errors:
        return render_template(
            "documents_to_sign.html",
            submission_id=submission_id,
            acceptance_value=acceptance_value,
            errors=errors,
            result=None,
        ), 400

    result = {
        "submission_id": submission_id,
        "form_title": submission["form_title"],
        "message": "Wniosek został zaakceptowany. Można przejść do podpisywania dokumentów.",
    }

    return render_template(
        "documents_to_sign.html",
        submission_id=submission_id,
        acceptance_value=acceptance_value,
        errors={},
        result=result,
    )


@app.route("/downloads/pdfs/<slug>/<path:filename>", methods=["GET"])
def download_pdf(slug: str, filename: str):
    try:
        pdf_bytes = storage.get_pdf_bytes(slug, filename)
        return send_file(
            BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
        )
    except Exception:
        abort(404)


@app.route("/downloads/signed/<slug>/<path:filename>", methods=["GET"])
def download_signed_pdf(slug: str, filename: str):
    try:
        pdf_bytes = storage.get_pdf_bytes(slug, filename)
        return send_file(
            BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
        )
    except Exception:
        abort(404)


if __name__ == "__main__":
    app.run(debug=app.config["DEBUG"], host="127.0.0.1", port=5000)
