import logging
from pathlib import Path
from uuid import uuid4
import base64
import mimetypes

from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, abort

from config import Config
from form_loader import (
    load_form_definition,
    validate_submission,
    extract_submission_data,
    build_submission_view,
    build_consents_view,
)
from pdf_generator import generate_pdf
from csv_exporter import append_submission
from signature_verifier import verify_signed_pdf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config.from_object(Config)

for directory in [
    app.config["OUTPUT_DIR"],
    app.config["PDF_OUTPUT_DIR"],
    app.config["CSV_OUTPUT_DIR"],
    app.config["SIGNED_OUTPUT_DIR"],
]:
    Path(directory).mkdir(parents=True, exist_ok=True)
    
    
    

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


def detect_form_files() -> list[Path]:
    forms_dir = Path("forms")
    forms_dir.mkdir(parents=True, exist_ok=True)
    return sorted(forms_dir.glob("*.json"))


def build_forms_registry() -> list[dict]:
    forms = []

    for index, file_path in enumerate(detect_form_files()):
        try:
            form_definition = load_form_definition(str(file_path))
            slug = file_path.stem

            forms.append(
                {
                    "slug": slug,
                    "title": form_definition.get("title") or slug_to_title(slug),
                    "description": form_definition.get("description", ""),
                    "definition_path": str(file_path),
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
            logger.warning("Nie udało się załadować formularza %s: %s", file_path, exc)

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
    return load_form_definition(form_meta["definition_path"])


def resolve_pdf_image_data_uri(app: Flask, form_definition: dict) -> str | None:
    image_value = form_definition.get("header_image")
    if not image_value:
        return None

    image_path = Path(image_value)

    if not image_path.is_absolute():
        image_path = Path(app.root_folder) / "static"/ image_value

    if not image_path.exists():
        logger.warning("Nie znaleziono obrazu do PDF: %s", image_path)
        return None

    mime_type, _ = mimetypes.guess_type(image_path.name)
    if not mime_type:
        mime_type = "image/png"

    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"

@app.context_processor
def inject_globals():
    return {
        "app_name": app.config["APP_NAME"],
    }


@app.route("/", methods=["GET"])
def index():
    forms = get_forms()
    return render_template("index.html", forms=forms)


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
        pdf_filename = f"{slug}-{submission_id}.pdf"
        pdf_path = Path(app.config["PDF_OUTPUT_DIR"]) / pdf_filename

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
        


        generate_pdf(
            app=app,
            template_name="pdf_template.html",
            context=pdf_context,
            output_path=pdf_path,
        )
        logger.info("Wygenerowano PDF: %s", pdf_path)

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

        append_submission(
            csv_file_path=Path(app.config["CSV_OUTPUT_DIR"]) / app.config["CSV_FILENAME"],
            form_definition=form_definition,
            row=csv_row,
        )

        result = {
            "submission_id": submission_id,
            "form_slug": slug,
            "pdf_filename": pdf_filename,
            "pdf_url": url_for("download_pdf", filename=pdf_filename),
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

        signed_pdf_filename = f"{slug}-{submission_id}-signed.pdf"
        signed_pdf_path = Path(app.config["SIGNED_OUTPUT_DIR"]) / signed_pdf_filename
        uploaded_file.save(signed_pdf_path)

        verification = verify_signed_pdf(signed_pdf_path)

        if not verification["is_signed"]:
            signed_pdf_path.unlink(missing_ok=True)
            flash("Przesłany plik nie zawiera podpisu PDF.", "error")
            return redirect(url_for("show_result", slug=slug, submission_id=submission_id))

        if not verification["is_szafir_signature"]:
            signed_pdf_path.unlink(missing_ok=True)
            flash("Przesłany plik nie jest podpisem Szafir / KIR.", "error")
            return redirect(url_for("show_result", slug=slug, submission_id=submission_id))

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

    pdf_filename = f"{slug}-{submission_id}.pdf"
    signed_pdf_filename = f"{slug}-{submission_id}-signed.pdf"
    signed_pdf_path = Path(app.config["SIGNED_OUTPUT_DIR"]) / signed_pdf_filename

    verification = None
    if signed_pdf_path.exists():
        try:
            verification = verify_signed_pdf(signed_pdf_path)
        except Exception as exc:
            logger.warning("Nie udało się odczytać podpisu: %s", exc)

    result = {
        "submission_id": submission_id,
        "form_slug": slug,
        "pdf_filename": pdf_filename,
        "pdf_url": url_for("download_pdf", filename=pdf_filename),
        "signature_request_id": "mobywatel-manual",
        "signature_status": (
            "szafir"
            if verification and verification.get("is_szafir_signature")
            else "uploaded" if signed_pdf_path.exists() else "manual"
        ),
        "signed_pdf_filename": signed_pdf_filename if signed_pdf_path.exists() else "",
        "signed_pdf_url": (
            url_for("download_signed_pdf", filename=signed_pdf_filename)
            if signed_pdf_path.exists()
            else None
        ),
        "upload_url": url_for("upload_signed_pdf", slug=slug, submission_id=submission_id),
        "form_title": form_definition["title"],
        "verification": verification,
    }

    return render_template("result.html", result=result)


@app.route("/downloads/pdfs/<path:filename>", methods=["GET"])
def download_pdf(filename: str):
    return send_from_directory(app.config["PDF_OUTPUT_DIR"], filename, as_attachment=True)


@app.route("/downloads/signed/<path:filename>", methods=["GET"])
def download_signed_pdf(filename: str):
    return send_from_directory(app.config["SIGNED_OUTPUT_DIR"], filename, as_attachment=True)




if __name__ == "__main__":
    app.run(debug=app.config["DEBUG"], host="127.0.0.1", port=5000)