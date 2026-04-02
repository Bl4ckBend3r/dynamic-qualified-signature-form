import csv
from pathlib import Path
from typing import Dict, Any


def build_csv_headers(form_definition: Dict[str, Any]) -> list[str]:
    dynamic_fields = []

    for field in form_definition["fields"]:
        if field["type"] in {"section", "static_text"}:
            continue
        dynamic_fields.append(field["name"])

    return [
        "submission_id",
        "created_at",
        "form_name",
        "pdf_filename",
        "signed_pdf_filename",
        "signature_status",
        "signature_request_id",
        *dynamic_fields,
    ]


def append_submission(csv_file_path: Path, form_definition: Dict[str, Any], row: Dict[str, Any]) -> None:
    csv_file_path.parent.mkdir(parents=True, exist_ok=True)

    headers = build_csv_headers(form_definition)
    file_exists = csv_file_path.exists()

    with open(csv_file_path, "a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=headers)

        if not file_exists:
            writer.writeheader()

        normalized_row = {header: row.get(header, "") for header in headers}
        writer.writerow(normalized_row)