import logging
from pathlib import Path
from uuid import uuid4
from signature_verifier import verify_signed_pdf


from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory

from config import Config
from form_loader import (
    load_form_definition,
    validate_submission,
    extract_submission_data,
    build_submission_view,
)
from pdf_generator import generate_pdf
from csv_exporter import append_submission

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

FORM_DEFINITION = load_form_definition(app.config["FORM_DEFINITION_PATH"])


@app.context_processor
def inject_globals():
    return {
        "app_name": app.config["APP_NAME"],
    }


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", form_definition=FORM_DEFINITION, errors={}, values={})


@app.route("/submit", methods=["POST"])
def submit():
    form_definition = FORM_DEFINITION
    submission_id = str(uuid4())

    submission_data = extract_submission_data(form_definition, request.form)
    errors = validate_submission(form_definition, submission_data)

    if errors:
        flash("Formularz zawiera błędy. Popraw wskazane pola.", "error")
        return render_template(
            "index.html",
            form_definition=form_definition,
            errors=errors,
            values=submission_data,
        ), 400

    try:
        pdf_filename = f"{submission_id}.pdf"
        pdf_path = Path(app.config["PDF_OUTPUT_DIR"]) / pdf_filename

        pdf_context = {
            "form_definition": form_definition,
            "submission_view": build_submission_view(form_definition, submission_data),
            "submission_id": submission_id,
        }

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
            "pdf_filename": pdf_filename,
            "pdf_url": url_for("download_pdf", filename=pdf_filename),
            "signature_request_id": "mobywatel-manual",
            "signature_status": signature_status,
            "signed_pdf_filename": signed_pdf_filename,
            "signed_pdf_url": None,
            "upload_url": url_for("upload_signed_pdf", submission_id=submission_id),
            "form_title": form_definition["title"],
        }

        return render_template("result.html", result=result)

    except Exception as exc:
        logger.exception("Błąd przetwarzania formularza: %s", exc)
        flash("Wystąpił błąd podczas przetwarzania formularza.", "error")
        return render_template(
            "index.html",
            form_definition=form_definition,
            errors={},
            values=submission_data,
        ), 500


@app.route("/upload-signed/<submission_id>", methods=["POST"])
def upload_signed_pdf(submission_id: str):
    try:
        uploaded_file = request.files.get("signed_pdf")

        if not uploaded_file or not uploaded_file.filename:
            flash("Nie wybrano pliku PDF.", "error")
            return redirect(url_for("show_result", submission_id=submission_id))

        if not uploaded_file.filename.lower().endswith(".pdf"):
            flash("Dozwolony jest wyłącznie plik PDF.", "error")
            return redirect(url_for("show_result", submission_id=submission_id))

        signed_pdf_filename = f"{submission_id}-signed.pdf"
        signed_pdf_path = Path(app.config["SIGNED_OUTPUT_DIR"]) / signed_pdf_filename
        uploaded_file.save(signed_pdf_path)

        verification = verify_signed_pdf(signed_pdf_path)

        if not verification["is_signed"]:
            signed_pdf_path.unlink(missing_ok=True)
            flash("Przesłany plik nie zawiera podpisu PDF.", "error")
            return redirect(url_for("show_result", submission_id=submission_id))

        if not verification["is_szafir_signature"]:
            signed_pdf_path.unlink(missing_ok=True)
            flash("Przesłany plik nie jest podpisem Szafir / KIR.", "error")
            return redirect(url_for("show_result", submission_id=submission_id))

        flash("Wykryto poprawny podpis Szafir / KIR.", "success")
        return redirect(url_for("show_result", submission_id=submission_id))

    except Exception as exc:
        logger.exception("Błąd uploadu podpisanego PDF: %s", exc)
        flash("Wystąpił błąd podczas wgrywania lub weryfikacji podpisu.", "error")
        return redirect(url_for("show_result", submission_id=submission_id))

@app.route("/result/<submission_id>", methods=["GET"])
def show_result(submission_id: str):
    pdf_filename = f"{submission_id}.pdf"
    signed_pdf_filename = f"{submission_id}-signed.pdf"
    signed_pdf_path = Path(app.config["SIGNED_OUTPUT_DIR"]) / signed_pdf_filename

    verification = None
    if signed_pdf_path.exists():
        try:
            verification = verify_signed_pdf(signed_pdf_path)
        except Exception as exc:
            logger.warning("Nie udało się odczytać podpisu: %s", exc)

    result = {
        "submission_id": submission_id,
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
        "upload_url": url_for("upload_signed_pdf", submission_id=submission_id),
        "form_title": FORM_DEFINITION["title"],
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