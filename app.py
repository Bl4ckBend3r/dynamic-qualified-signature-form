import logging
from pathlib import Path
from uuid import uuid4

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
from signature_service import build_signature_provider

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
    app.config["SIGNATURE_WORK_DIR"],
]:
    Path(directory).mkdir(parents=True, exist_ok=True)

FORM_DEFINITION = load_form_definition(app.config["FORM_DEFINITION_PATH"])
SIGNATURE_PROVIDER = build_signature_provider(app.config)


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

        signature_request = SIGNATURE_PROVIDER.create_signature_request(
            submission_id=submission_id,
            pdf_path=pdf_path,
            form_name=form_definition["title"],
            metadata={"fields": submission_data},
        )

        SIGNATURE_PROVIDER.submit_document_for_signature(signature_request["request_id"])
        signature_status = SIGNATURE_PROVIDER.get_signature_status(signature_request["request_id"])

        signed_pdf_filename = ""
        signed_pdf_path = ""

        if signature_status == "signed":
            saved_signed_path = SIGNATURE_PROVIDER.save_signed_document(
                signature_request["request_id"],
                Path(app.config["SIGNED_OUTPUT_DIR"])
            )
            signed_pdf_filename = saved_signed_path.name
            signed_pdf_path = str(saved_signed_path)
        elif signature_status == "failed":
            logger.warning("Proces podpisu zakończył się statusem failed dla %s", submission_id)
        else:
            logger.info("Proces podpisu pozostaje w statusie pending dla %s", submission_id)

        csv_row = {
            "submission_id": submission_id,
            "created_at": signature_request["created_at"],
            "form_name": form_definition["title"],
            "pdf_filename": pdf_filename,
            "signed_pdf_filename": signed_pdf_filename,
            "signature_status": signature_status,
            "signature_request_id": signature_request["request_id"],
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
            "signature_request_id": signature_request["request_id"],
            "signature_status": signature_status,
            "signed_pdf_filename": signed_pdf_filename,
            "signed_pdf_url": (
                url_for("download_signed_pdf", filename=signed_pdf_filename)
                if signed_pdf_filename
                else None
            ),
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


@app.route("/downloads/pdfs/<path:filename>", methods=["GET"])
def download_pdf(filename: str):
    return send_from_directory(app.config["PDF_OUTPUT_DIR"], filename, as_attachment=True)


@app.route("/downloads/signed/<path:filename>", methods=["GET"])
def download_signed_pdf(filename: str):
    return send_from_directory(app.config["SIGNED_OUTPUT_DIR"], filename, as_attachment=True)


if __name__ == "__main__":
    app.run(debug=app.config["DEBUG"], host="127.0.0.1", port=5000)