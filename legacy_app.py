# Legacy compatibility module.
#
# Runtime create_app() does not import this file and does not register any
# endpoints defined here. Keep this module for historical direct execution,
# compatibility wrappers, and regression tests only.

import base64
import csv
import json
import logging
import mimetypes
import re
import tempfile
from datetime import date, datetime
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
from werkzeug.exceptions import HTTPException

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
from services.access_token_service import AccessTokenService
from services.document_service import (
    DocumentType,
    build_agreement_filename,
    build_declaration_filename,
    build_document_pdf_context,
    generate_document_pdf_bytes,
    get_document_config,
    is_document_enabled,
)
from services import declaration_service as legacy_declaration_service
from services.documents import document_generation_service as legacy_document_generation_service
from services.process_service import (
    OfficerDecision,
    ProcessStatus,
    build_initial_process_fields,
    build_legacy_process_fields,
    build_process_state,
    get_officer_decision,
    is_agreement_required,
    should_send_officer_decision_email,
)
from services.training_agreement_service import (
    build_training_agreement_number as service_build_training_agreement_number,
    extract_training_selection as service_extract_training_selection,
    get_training_selection_field as service_get_training_selection_field,
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
access_token_service = AccessTokenService()
submission_repository = None

logger.info("NEXTCLOUD_BASE_URL=%s", app.config["NEXTCLOUD_BASE_URL"])
logger.info("NEXTCLOUD_USERNAME=%s", app.config["NEXTCLOUD_USERNAME"])
logger.info("NEXTCLOUD_FORMS_DIR=%s", app.config["NEXTCLOUD_FORMS_DIR"])
logger.info("NEXTCLOUD_OUTPUT_DIR=%s", app.config["NEXTCLOUD_OUTPUT_DIR"])


class LegacyPdfRenderAdapter:
    def render_document_pdf_bytes(self, **kwargs) -> bytes:
        return generate_document_pdf_bytes(**kwargs)


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


def build_signed_declaration_filename(declaration_filename: str) -> str:
    declaration_path = Path(declaration_filename)
    stem = declaration_path.stem or "deklaracja"
    suffix = declaration_path.suffix or ".pdf"
    return f"{stem}-signed{suffix}"


def build_pdf_download_url(slug: str, filename: str, submission: dict | None = None) -> str:
    values = {"slug": slug, "filename": filename}
    access_token = str((submission or {}).get("access_token") or "").strip()
    if access_token:
        values["token"] = access_token
    return url_for("documents.download_pdf", **values)


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


def find_submission_by_pdf(slug: str, filename: str) -> dict | None:
    filename = Path(filename).name
    for row in read_submission_rows(slug):
        for agreement in parse_training_agreements(row):
            if filename in {
                agreement.get("filename", ""),
                agreement.get("signed_filename", ""),
            }:
                return row
        known_filenames = {
            row.get("pdf_filename", ""),
            row.get("declaration_filename", ""),
            row.get("agreement_filename", ""),
        }
        if filename in known_filenames:
            return row
    return None


def resolve_nextcloud_template_html(template_path: str) -> str | None:
    normalized_path = str(template_path or "").replace("\\", "/").strip().strip("/")

    if not normalized_path:
        return None

    forms_dir = app.config["NEXTCLOUD_FORMS_DIR"].strip("/")
    output_dir = app.config["NEXTCLOUD_OUTPUT_DIR"].strip("/")

    if not normalized_path.startswith((f"{forms_dir}/", f"{output_dir}/")):
        normalized_path = f"{forms_dir}/{normalized_path}"

    template_html = storage.read_text_or_empty(normalized_path)

    if not template_html.strip():
        raise RuntimeError(f"Nie znaleziono szablonu dokumentu w Nextcloud: {normalized_path}")

    return template_html


def normalize_nextcloud_asset_path(asset_path: str) -> str:
    normalized = str(asset_path or "").replace("\\", "/").strip().strip("/")
    forms_dir = app.config["NEXTCLOUD_FORMS_DIR"].strip("/")
    output_dir = app.config["NEXTCLOUD_OUTPUT_DIR"].strip("/")

    if normalized.startswith((f"{forms_dir}/", f"{output_dir}/")):
        return normalized

    return f"{forms_dir}/{normalized}"


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


def normalize_training_id(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "").strip())
    return normalized.strip("_") or "szkolenie"


def parse_json_list(value: str | list | None) -> list[dict]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    raw = str(value or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def parse_selected_trainings(row: dict) -> list[dict]:
    return parse_json_list(row.get("selected_trainings"))


def parse_training_agreements(row: dict) -> list[dict]:
    return parse_json_list(row.get("training_agreements"))


def serialize_json_list(items: list[dict]) -> str:
    return json.dumps(items, ensure_ascii=False)


def get_training_selection_field(form_definition: dict) -> dict | None:
    return service_get_training_selection_field(form_definition)


def extract_training_selection(field: dict, request_form) -> tuple[list[dict], str | None]:
    return service_extract_training_selection(field, request_form)


def get_training_agreement_config(form_definition: dict) -> dict:
    process = form_definition.get("process") or {}
    documents = process.get("documents") if isinstance(process, dict) else {}
    documents = documents if isinstance(documents, dict) else {}
    config = documents.get("training_agreement") or documents.get("agreement") or {}
    if not isinstance(config, dict):
        config = {}
    return {
        "enabled": bool(config.get("enabled", True)),
        "template": config.get("template", ""),
        "filename_pattern": config.get("filename_pattern") or "{first_name}_{last_name}-{training_id}-umowa.pdf",
        "signature_required": bool(config.get("signature_required", True)),
        "repeat_over": config.get("repeat_over") or "selected_trainings",
        "repeat_item_alias": config.get("repeat_item_alias") or "training",
        "numbering": config.get("numbering") or {
            "number_pattern": "{submission_id}/{agreement_sequence}/{generated_date}",
            "date_format": "%Y-%m-%d",
        },
    }


def is_training_agreement_enabled(form_definition: dict) -> bool:
    return bool(get_training_agreement_config(form_definition).get("enabled"))


def build_training_agreement_number(
    submission_id: str,
    sequence: int,
    generated_date: str,
    config: dict,
) -> str:
    return service_build_training_agreement_number(submission_id, sequence, generated_date, config)


def build_training_agreement_filename(pattern: str, row: dict, training: dict, sequence: int) -> str:
    return legacy_document_generation_service.build_training_agreement_filename(pattern, row, training, sequence)


def ensure_declaration_generated(submission: dict, force: bool = False) -> dict:
    return legacy_declaration_service.ensure_declaration_generated(
        submission,
        app=app,
        storage=storage,
        get_form_definition=get_form_definition,
        resolve_template_html=resolve_nextcloud_template_html,
        resolve_pdf_image_url=resolve_pdf_image_url,
        force=force,
        submission_repository=submission_repository,
        pdf_render_service=LegacyPdfRenderAdapter(),
        log=logger,
    )


def build_signature_update_fields(
    verification: dict,
    signed_filename: str,
    row: dict,
) -> dict[str, str]:
    is_signed = bool(verification.get("is_signed"))
    is_valid = bool(verification.get("is_allowed_signature"))
    signature_type = verification.get("signature_type") or "unknown"

    return {
        "declaration_signed": "Tak" if is_signed else "Nie",
        "declaration_signed_filename": signed_filename if is_valid else "",
        "declaration_signature_type": signature_type,
        "declaration_signature_valid": "Tak" if is_valid else "Nie",
        "declaration_signature_error": "" if is_valid else verification.get("reason", "Niepoprawny podpis deklaracji."),
        "process_status": (
            ProcessStatus.AGREEMENT_READY.value
            if is_valid and is_agreement_required(row)
            else ProcessStatus.PARTICIPANT_ACCEPTED.value
            if is_valid
            else ProcessStatus.DECLARATION_SIGNATURE_INVALID.value
        ),
    }


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


def build_declaration_form_definition(form_definition: dict, declaration_config: dict) -> dict:
    return legacy_declaration_service.build_declaration_form_definition(declaration_config)


def build_agreement_block_updates(declaration_data: dict) -> dict[str, str]:
    return legacy_document_generation_service.build_agreement_block_updates(declaration_data)


def generate_training_agreements_for_submission(
    submission: dict,
    generated_date: str | None = None,
) -> list[dict]:
    return legacy_document_generation_service.generate_training_agreements_for_submission(
        submission,
        app=app,
        storage=storage,
        get_form_definition=get_form_definition,
        get_training_agreement_config=get_training_agreement_config,
        parse_selected_trainings=parse_selected_trainings,
        resolve_template_html=resolve_nextcloud_template_html,
        resolve_pdf_image_url=resolve_pdf_image_url,
        generated_date=generated_date,
        submission_repository=submission_repository,
        pdf_render_service=LegacyPdfRenderAdapter(),
    )


# TODO(P3.x): legacy-only endpoint. Runtime create_app() registers routes/documents.py instead.
@app.get("/nextcloud-assets/<path:asset_path>", endpoint="nextcloud_asset")
def nextcloud_asset(asset_path: str):
    resolved_path = normalize_nextcloud_asset_path(asset_path)

    try:
        if hasattr(storage, "read_bytes"):
            file_bytes = storage.read_bytes(resolved_path)
        else:
            file_bytes = storage.get_file_bytes(resolved_path)
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


# TODO(P3.x): legacy-only endpoint. Runtime create_app() registers routes/public_forms.py instead.
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


# TODO(P3.x): legacy-only endpoint. Runtime create_app() registers routes/public_forms.py instead.
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


# TODO(P3.x): legacy-only endpoint. Runtime create_app() registers routes/public_forms.py instead.
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
    submission_data.update(
        build_initial_process_fields(
            declaration_required=is_document_enabled(form_definition, DocumentType.DECLARATION),
            agreement_required=(
                is_document_enabled(form_definition, DocumentType.AGREEMENT)
                or is_training_agreement_enabled(form_definition)
            ),
        )
    )
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
            "created_at": datetime.now().strftime("%d.%m.%Y"),
            "form_name": form_definition["title"],
            "access_token": access_token_service.generate_token(),
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
            "pdf_url": url_for(
                "download_pdf",
                slug=slug,
                filename=pdf_filename,
                token=csv_row["access_token"],
            ),
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


# TODO(P3.x): legacy-only endpoint. Runtime create_app() registers routes/documents.py instead.
@app.route("/upload-declaration-signed/<slug>/<submission_id>", methods=["POST"])
def upload_signed_declaration(slug: str, submission_id: str):
    submission = find_submission_acceptance_by_id(submission_id)

    if not submission or submission["form_slug"] != slug:
        flash("Nie znaleziono wniosku dla podpisanej deklaracji.", "error")
        return redirect(url_for("documents_to_sign"))

    if not submission["can_sign_documents"]:
        flash("Wniosek nie został zaakceptowany przez urzędnika.", "error")
        return redirect(url_for("documents_to_sign"))

    row = submission["row"]
    declaration_filename = row.get("declaration_filename", "").strip()

    if not declaration_filename:
        flash("Najpierw wygeneruj deklarację do podpisu.", "error")
        return redirect(url_for("documents_to_sign"))

    uploaded_file = request.files.get("signed_declaration_pdf")

    if not uploaded_file or not uploaded_file.filename:
        flash("Nie wybrano podpisanej deklaracji PDF.", "error")
        return redirect(url_for("documents_to_sign"))

    if not uploaded_file.filename.lower().endswith(".pdf"):
        flash("Dozwolony jest wyłącznie plik PDF.", "error")
        return redirect(url_for("documents_to_sign"))

    signed_filename = build_signed_declaration_filename(declaration_filename)
    uploaded_bytes = uploaded_file.read()

    with tempfile.NamedTemporaryFile(
        suffix=".pdf",
        delete=False,
        dir=app.config["TEMP_DIR"],
    ) as tmp_signed:
        tmp_signed_path = Path(tmp_signed.name)
        tmp_signed.write(uploaded_bytes)

    try:
        try:
            verification = verify_signed_pdf(tmp_signed_path)
        finally:
            tmp_signed_path.unlink(missing_ok=True)

        update_fields = build_signature_update_fields(verification, signed_filename, row)
        signature_is_valid = update_fields["declaration_signature_valid"] == "Tak"

        if signature_is_valid:
            storage.save_pdf(slug, signed_filename, uploaded_bytes)

        storage.update_csv_row_by_submission_id(slug, submission_id, update_fields)

        if not verification.get("is_signed"):
            flash("Przesłany plik nie zawiera podpisu PDF.", "error")
        elif not signature_is_valid:
            flash("Podpis deklaracji nie jest dopuszczalnym podpisem mSzafir ani Profilem Zaufanym.", "error")
        else:
            flash("Deklaracja została podpisana i poprawnie zweryfikowana.", "success")

    except Exception as exc:
        logger.exception("Błąd uploadu podpisanej deklaracji: %s", exc)
        flash("Wystąpił błąd podczas wgrywania lub weryfikacji deklaracji.", "error")

    return redirect(url_for("documents_to_sign"))


# TODO(P3.x): legacy-only endpoint. Runtime create_app() registers routes/documents.py instead.
@app.route("/declaration/<slug>/<submission_id>", methods=["GET", "POST"])
def declaration_form(slug: str, submission_id: str):
    submission = find_submission_acceptance_by_id(submission_id)

    if not submission or submission["form_slug"] != slug:
        flash("Nie znaleziono wniosku dla deklaracji.", "error")
        return redirect(url_for("documents_to_sign"))

    if not submission["can_sign_documents"]:
        flash("Wniosek nie został zaakceptowany przez urzędnika.", "error")
        return redirect(url_for("documents_to_sign"))

    form_definition = get_form_definition(slug)
    if not form_definition:
        abort(404)

    declaration_config = get_document_config(form_definition, DocumentType.DECLARATION)
    if not declaration_config.get("enabled"):
        flash("Deklaracja nie jest wymagana dla tego formularza.", "info")
        return redirect(url_for("documents_to_sign"))

    declaration_definition = build_declaration_form_definition(form_definition, declaration_config)
    values = dict(submission["row"])
    errors = {}

    if request.method == "POST":
        declaration_data = extract_submission_data(declaration_definition, request.form)
        values.update(declaration_data)
        errors = validate_submission(declaration_definition, declaration_data)
        training_field = get_training_selection_field(form_definition)

        if training_field:
            selected_trainings, training_error = extract_training_selection(training_field, request.form)
            declaration_data["selected_trainings"] = serialize_json_list(selected_trainings)
            values["selected_trainings"] = declaration_data["selected_trainings"]
            if training_error:
                errors[training_field.get("name", "selected_trainings")] = training_error

        if not errors:
            updates = {
                **declaration_data,
                **build_agreement_block_updates(declaration_data),
            }
            storage.update_csv_row_by_submission_id(slug, submission_id, updates)
            refreshed_submission = find_submission_acceptance_by_id(submission_id)
            if refreshed_submission:
                try:
                    ensure_declaration_generated(refreshed_submission, force=True)
                    flash("Deklaracja została wygenerowana.", "success")
                except Exception as exc:
                    logger.exception("Nie udało się wygenerować deklaracji: %s", exc)
                    flash("Nie udało się wygenerować deklaracji.", "error")
            return redirect(url_for("documents_to_sign"))

        flash("Deklaracja zawiera błędy. Popraw wskazane pola.", "error")

    return render_template(
        "declaration_form.html",
        form_definition=declaration_definition,
        action_url=url_for("declaration_form", slug=slug, submission_id=submission_id),
        errors=errors,
        values=values,
    )


# TODO(P3.x): legacy-only endpoint. Runtime create_app() registers routes/documents.py instead.
@app.route("/agreements/<slug>/<submission_id>/generate", methods=["POST"])
def generate_training_agreements(slug: str, submission_id: str):
    submission = find_submission_acceptance_by_id(submission_id)

    if not submission or submission["form_slug"] != slug:
        flash("Nie znaleziono wniosku dla umów.", "error")
        return redirect(url_for("documents_to_sign"))

    row = submission["row"]
    if row.get("declaration_signature_valid", "").strip().lower() != "tak":
        flash("Najpierw wgraj poprawnie podpisaną deklarację.", "error")
        return redirect(url_for("documents_to_sign"))

    generated_date = request.form.get("agreement_generated_at", "").strip() or date.today().isoformat()

    try:
        agreements = generate_training_agreements_for_submission(submission, generated_date)
        flash(f"Wygenerowano umowy: {len(agreements)}.", "success")
    except Exception as exc:
        logger.exception("Nie udało się wygenerować umów szkoleniowych: %s", exc)
        flash("Nie udało się wygenerować umów szkoleniowych.", "error")

    return redirect(url_for("documents_to_sign"))


# TODO(P3.x): legacy-only endpoint. Runtime create_app() registers routes/documents.py instead.
@app.route("/agreements/<slug>/<submission_id>/<agreement_id>/upload", methods=["POST"])
def upload_signed_training_agreement(slug: str, submission_id: str, agreement_id: str):
    submission = find_submission_acceptance_by_id(submission_id)

    if not submission or submission["form_slug"] != slug:
        flash("Nie znaleziono wniosku dla podpisanej umowy.", "error")
        return redirect(url_for("documents_to_sign"))

    row = submission["row"]
    agreements = parse_training_agreements(row)
    agreement = next((item for item in agreements if item.get("id") == agreement_id), None)
    if not agreement:
        flash("Nie znaleziono umowy dla wybranego szkolenia.", "error")
        return redirect(url_for("documents_to_sign"))

    uploaded_file = request.files.get("signed_agreement_pdf")
    if not uploaded_file or not uploaded_file.filename:
        flash("Nie wybrano podpisanej umowy PDF.", "error")
        return redirect(url_for("documents_to_sign"))

    if not uploaded_file.filename.lower().endswith(".pdf"):
        flash("Dozwolony jest wyłącznie plik PDF.", "error")
        return redirect(url_for("documents_to_sign"))

    uploaded_bytes = uploaded_file.read()
    source_filename = agreement.get("filename") or f"{agreement_id}-umowa.pdf"
    signed_filename = f"{Path(source_filename).stem}-signed{Path(source_filename).suffix or '.pdf'}"

    with tempfile.NamedTemporaryFile(
        suffix=".pdf",
        delete=False,
        dir=app.config["TEMP_DIR"],
    ) as tmp_signed:
        tmp_signed_path = Path(tmp_signed.name)
        tmp_signed.write(uploaded_bytes)

    try:
        try:
            verification = verify_signed_pdf(tmp_signed_path)
        finally:
            tmp_signed_path.unlink(missing_ok=True)

        is_signed = bool(verification.get("is_signed"))
        is_valid = bool(verification.get("is_allowed_signature") or verification.get("is_szafir_signature"))
        agreement.update(
            {
                "signed": is_signed,
                "signature_valid": is_valid,
                "signed_filename": signed_filename if is_valid else "",
                "signature_type": verification.get("signature_type") or "unknown",
                "signature_error": "" if is_valid else verification.get("reason", "Niepoprawny podpis umowy."),
            }
        )

        if is_valid:
            storage.save_pdf(slug, signed_filename, uploaded_bytes)

        all_valid = all(bool(item.get("signature_valid")) for item in agreements)
        storage.update_csv_row_by_submission_id(
            slug,
            submission_id,
            {
                "training_agreements": serialize_json_list(agreements),
                "agreement_signed": "Tak" if all_valid else "",
                "agreement_signature_valid": "Tak" if all_valid else "",
                "agreement_signed_filename": signed_filename if all_valid else "",
                "process_status": (
                    ProcessStatus.PARTICIPANT_ACCEPTED.value
                    if all_valid
                    else ProcessStatus.AGREEMENT_WAITING_FOR_SIGNATURE.value
                ),
            },
        )

        if not is_signed:
            flash("Przesłany plik nie zawiera podpisu PDF.", "error")
        elif not is_valid:
            flash("Podpis umowy nie jest dopuszczalnym podpisem.", "error")
        else:
            flash("Umowa została podpisana i poprawnie zweryfikowana.", "success")

    except Exception as exc:
        logger.exception("Błąd uploadu podpisanej umowy: %s", exc)
        flash("Wystąpił błąd podczas wgrywania lub weryfikacji umowy.", "error")

    return redirect(url_for("documents_to_sign"))


# TODO(P3.x): legacy-only endpoint. Runtime create_app() registers routes/documents.py instead.
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


# TODO(P3.x): legacy-only endpoint. Runtime create_app() registers routes/public_forms.py instead.
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


# TODO(P3.x): legacy-only endpoint. Runtime create_app() registers routes/api.py instead.
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


# TODO(P3.x): legacy-only endpoint. Runtime create_app() registers routes/documents.py instead.
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

    try:
        declaration = ensure_declaration_generated(submission)
    except Exception as exc:
        logger.exception("Nie udało się wygenerować deklaracji: %s", exc)
        errors["submission_id"] = "Nie udało się wygenerować deklaracji do podpisu."
        return render_template(
            "documents_to_sign.html",
            submission_id=submission_id,
            acceptance_value=acceptance_value,
            errors=errors,
            result=None,
        ), 500

    result = {
        "submission_id": submission_id,
        "form_slug": submission["form_slug"],
        "form_title": submission["form_title"],
        "message": (
            "Deklaracja została wygenerowana i jest gotowa do podpisania."
            if declaration.get("enabled") and declaration["created"]
            else "Deklaracja była już wygenerowana i jest gotowa do podpisania."
            if declaration.get("enabled")
            else "Dla tego formularza deklaracja nie jest wymagana."
        ),
        "declaration_filename": declaration["filename"],
        "declaration_url": (
            url_for(
                "download_pdf",
                slug=submission["form_slug"],
                filename=declaration["filename"],
                token=submission["row"].get("access_token", ""),
            )
            if declaration.get("enabled") and declaration.get("filename")
            else None
        ),
        "declaration_upload_url": (
            url_for(
                "upload_signed_declaration",
                slug=submission["form_slug"],
                submission_id=submission_id,
            )
            if declaration.get("enabled")
            else None
        ),
    }

    refreshed_submission = find_submission_acceptance_by_id(submission_id) or submission
    row = refreshed_submission["row"]
    process_state = build_process_state(row)
    training_agreements = parse_training_agreements(row)
    today_iso = date.today().isoformat()
    result.update(
        {
            "form_slug": refreshed_submission["form_slug"],
            "form_title": refreshed_submission["form_title"],
            "process_status": process_state.status.value,
            "declaration_url": (
                url_for(
                    "download_pdf",
                    slug=refreshed_submission["form_slug"],
                    filename=declaration["filename"],
                    token=row.get("access_token", ""),
                )
                if declaration.get("enabled") and declaration.get("filename")
                else None
            ),
            "declaration_upload_url": (
                url_for(
                    "upload_signed_declaration",
                    slug=refreshed_submission["form_slug"],
                    submission_id=submission_id,
                )
                if declaration.get("enabled")
                else None
            ),
            "declaration_signature_valid": row.get("declaration_signature_valid", "").strip().lower() == "tak",
            "agreement_blocked": row.get("agreement_blocked", "").strip().lower() == "tak",
            "agreement_block_reason": row.get("agreement_block_reason", ""),
            "can_generate_agreement": (
                process_state.can_generate_agreement
                or (
                    row.get("declaration_signature_valid", "").strip().lower() == "tak"
                    and row.get("agreement_generated", "").strip().lower() != "tak"
                    and row.get("agreement_blocked", "").strip().lower() != "tak"
                )
            ) and bool(parse_selected_trainings(row)),
            "generate_agreement_url": url_for(
                "generate_training_agreements",
                slug=refreshed_submission["form_slug"],
                submission_id=submission_id,
            ),
            "agreement_generated": row.get("agreement_generated", "").strip().lower() == "tak",
            "agreement_generated_at": row.get("agreement_generated_at", ""),
            "agreement_generated_at_iso": row.get("agreement_generated_at", "") or today_iso,
            "agreement_signature_valid": row.get("agreement_signature_valid", "").strip().lower() == "tak",
            "training_agreements": [
                {
                    **agreement,
                    "url": (
                        url_for(
                            "download_pdf",
                            slug=refreshed_submission["form_slug"],
                            filename=agreement.get("filename", ""),
                            token=row.get("access_token", ""),
                        )
                        if agreement.get("filename")
                        else ""
                    ),
                    "upload_url": url_for(
                        "upload_signed_training_agreement",
                        slug=refreshed_submission["form_slug"],
                        submission_id=submission_id,
                        agreement_id=agreement.get("id", ""),
                    ),
                }
                for agreement in training_agreements
            ],
        }
    )

    return render_template(
        "documents_to_sign.html",
        submission_id=submission_id,
        acceptance_value=acceptance_value,
        errors={},
        result=result,
    )


# TODO(P3.x): legacy-only endpoint. Runtime create_app() registers routes/documents.py instead.
@app.route("/downloads/pdfs/<slug>/<path:filename>", methods=["GET"])
def download_pdf(slug: str, filename: str):
    try:
        submission = find_submission_by_pdf(slug, filename)
        if submission and not access_token_service.verify_token(submission, request.args.get("token")):
            abort(403)
        pdf_bytes = storage.get_pdf_bytes(slug, filename)
    except HTTPException:
        raise
    except Exception:
        submission = find_submission_by_pdf(slug, filename)
        if not submission or Path(filename).name != str(submission.get("declaration_filename") or "").strip():
            abort(404)

        try:
            ensure_declaration_generated(
                {
                    "submission_id": submission.get("submission_id", ""),
                    "form_slug": slug,
                    "form_title": submission.get("form_name") or slug,
                    "row": submission,
                }
            )
            pdf_bytes = storage.get_pdf_bytes(slug, filename)
        except Exception:
            abort(404)

    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


# TODO(P3.x): legacy-only endpoint. Runtime create_app() registers routes/documents.py instead.
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
