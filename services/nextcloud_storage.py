import csv
import io
import json
import logging
import os
from pathlib import Path
from typing import Union
from urllib.parse import quote, unquote, urlparse
import xml.etree.ElementTree as ET

import requests

logger = logging.getLogger(__name__)


class NextcloudStorageError(Exception):
    pass


class NextcloudStorage:
    PDF_DECLARATION_DIR = "deklaracja"
    PDF_AGREEMENT_DIR = "umowy"
    PDF_SIGNED_DIR = "podpisane"
    PDF_UNSIGNED_DIR = "niepodpisane"

    def __init__(
        self,
        base_url: str,
        username: str,
        app_password: str,
        forms_dir: str = "Formularze",
        output_dir: str = "output",
        csv_filename: str = "dane.csv",
        timeout: int = 30,
        verify_ssl: Union[bool, str] = True,
    ) -> None:
        if not base_url:
            raise ValueError("NEXTCLOUD_BASE_URL is required")
        if not username:
            raise ValueError("NEXTCLOUD_USERNAME is required")
        if not app_password:
            raise ValueError("NEXTCLOUD_APP_PASSWORD is required")

        self.base_url = base_url.rstrip("/")
        self.username = username
        self.app_password = app_password
        self.forms_dir = forms_dir.strip("/")
        self.output_dir = output_dir.strip("/")
        self.csv_filename = csv_filename.strip("/") or "dane.csv"
        self.timeout = timeout
        self.verify_ssl = self._normalize_verify_ssl(verify_ssl)

        self.auth = (self.username, self.app_password)
        self.dav_root = f"{self.base_url}/remote.php/dav/files/{quote(self.username)}"

        logger.info("Nextcloud verify_ssl=%r", self.verify_ssl)
        logger.info("Nextcloud DAV root=%s", self.dav_root)
        logger.info("Nextcloud CSV filename=%s", self.csv_filename)

        if self.verify_ssl is False:
            logger.warning("Nextcloud SSL verification is DISABLED. Use only for local diagnostics.")

    @staticmethod
    def _normalize_verify_ssl(value: Union[bool, str]) -> Union[bool, str]:
        if isinstance(value, bool):
            return value

        if isinstance(value, str):
            normalized = value.strip()
            lowered = normalized.lower()

            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False

            return normalized

        return True

    def _encode_path(self, path: str) -> str:
        clean = path.strip("/")

        if not clean:
            return self.dav_root

        encoded = "/".join(quote(part) for part in clean.split("/"))
        return f"{self.dav_root}/{encoded}"

    def _csv_path(self, slug: str) -> str:
        return f"{self.output_dir}/{slug}/{self.csv_filename}"

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = self._encode_path(path)

        try:
            response = requests.request(
                method=method,
                url=url,
                auth=self.auth,
                timeout=self.timeout,
                verify=self.verify_ssl,
                **kwargs,
            )
            return response

        except requests.exceptions.SSLError as exc:
            verify_description = (
                self.verify_ssl if isinstance(self.verify_ssl, str) else str(self.verify_ssl)
            )
            raise NextcloudStorageError(
                f"SSL error for URL '{url}'. "
                f"verify={verify_description!r}. "
                f"Check server certificate chain or configure "
                f"NEXTCLOUD_CA_BUNDLE / NEXTCLOUD_VERIFY_SSL. "
                f"Original error: {exc}"
            ) from exc

        except requests.exceptions.RequestException as exc:
            raise NextcloudStorageError(
                f"HTTP request failed for URL '{url}'. Original error: {exc}"
            ) from exc

    def _check_status(
        self,
        response: requests.Response,
        allowed_statuses: tuple[int, ...],
        message: str,
    ) -> None:
        if response.status_code not in allowed_statuses:
            raise NextcloudStorageError(
                f"{message}. HTTP {response.status_code}. Response: {response.text}"
            )

    def exists(self, path: str) -> bool:
        response = self._request("PROPFIND", path, headers={"Depth": "0"})

        if response.status_code in (200, 207):
            return True

        for fallback_path in self._pdf_lookup_paths_from_storage_path(path):
            fallback_response = self._request("PROPFIND", fallback_path, headers={"Depth": "0"})
            if fallback_response.status_code in (200, 207):
                return True

        return False

    def mkdir(self, path: str) -> None:
        if self.exists(path):
            return

        response = self._request("MKCOL", path)

        if response.status_code in (201, 405):
            return

        raise NextcloudStorageError(
            f"Cannot create directory '{path}'. "
            f"HTTP {response.status_code}. Response: {response.text}"
        )

    def ensure_base_structure(self) -> None:
        self.mkdir(self.forms_dir)
        self.mkdir(self.output_dir)

    def ensure_form_output_structure(self, slug: str) -> None:
        pdf_root = f"{self.output_dir}/{slug}/pdf"

        self.mkdir(f"{self.output_dir}/{slug}")
        self.mkdir(pdf_root)
        self.mkdir(f"{pdf_root}/{self.PDF_DECLARATION_DIR}")
        self.mkdir(f"{pdf_root}/{self.PDF_DECLARATION_DIR}/{self.PDF_SIGNED_DIR}")
        self.mkdir(f"{pdf_root}/{self.PDF_DECLARATION_DIR}/{self.PDF_UNSIGNED_DIR}")
        self.mkdir(f"{pdf_root}/{self.PDF_AGREEMENT_DIR}")
        self.mkdir(f"{pdf_root}/{self.PDF_AGREEMENT_DIR}/{self.PDF_SIGNED_DIR}")
        self.mkdir(f"{pdf_root}/{self.PDF_AGREEMENT_DIR}/{self.PDF_UNSIGNED_DIR}")

    def _normalize_pdf_document_type(self, document_type: str | None) -> str | None:
        normalized = str(document_type or "").strip().lower()

        if normalized in {"declaration", "deklaracja", "declarations", "deklaracje"}:
            return self.PDF_DECLARATION_DIR

        if normalized in {"agreement", "umowa", "umowy", "agreements"}:
            return self.PDF_AGREEMENT_DIR

        return None

    def _infer_pdf_document_type_from_filename(self, filename: str) -> str | None:
        normalized = Path(filename).name.lower()

        if "deklar" in normalized or "declaration" in normalized:
            return self.PDF_DECLARATION_DIR

        if "umow" in normalized or "agreement" in normalized:
            return self.PDF_AGREEMENT_DIR

        return None

    def _infer_pdf_signed_from_filename(self, filename: str) -> bool:
        normalized = Path(filename).name.lower()
        return any(marker in normalized for marker in ("-signed", "_signed", "podpisane", "podpisany"))

    def _pdf_directory(
        self,
        slug: str,
        document_type: str | None = None,
        signed: bool | None = None,
    ) -> str:
        pdf_root = f"{self.output_dir}/{slug}/pdf"
        normalized_document_type = self._normalize_pdf_document_type(document_type)

        if not normalized_document_type:
            return pdf_root

        signature_dir = self.PDF_SIGNED_DIR if signed else self.PDF_UNSIGNED_DIR
        return f"{pdf_root}/{normalized_document_type}/{signature_dir}"

    def _pdf_lookup_paths(self, slug: str, filename: str) -> list[str]:
        pdf_root = f"{self.output_dir}/{slug}/pdf"
        clean_filename = filename.strip("/")

        if "/" in clean_filename:
            return [f"{pdf_root}/{clean_filename}"]

        inferred_document_type = self._infer_pdf_document_type_from_filename(clean_filename)
        inferred_signed = self._infer_pdf_signed_from_filename(clean_filename)

        paths = []

        if inferred_document_type:
            preferred_signature_dir = self.PDF_SIGNED_DIR if inferred_signed else self.PDF_UNSIGNED_DIR
            fallback_signature_dir = self.PDF_UNSIGNED_DIR if inferred_signed else self.PDF_SIGNED_DIR
            paths.extend(
                [
                    f"{pdf_root}/{inferred_document_type}/{preferred_signature_dir}/{clean_filename}",
                    f"{pdf_root}/{inferred_document_type}/{fallback_signature_dir}/{clean_filename}",
                ]
            )

        paths.extend(
            [
                f"{pdf_root}/{self.PDF_DECLARATION_DIR}/{self.PDF_UNSIGNED_DIR}/{clean_filename}",
                f"{pdf_root}/{self.PDF_DECLARATION_DIR}/{self.PDF_SIGNED_DIR}/{clean_filename}",
                f"{pdf_root}/{self.PDF_AGREEMENT_DIR}/{self.PDF_UNSIGNED_DIR}/{clean_filename}",
                f"{pdf_root}/{self.PDF_AGREEMENT_DIR}/{self.PDF_SIGNED_DIR}/{clean_filename}",
                f"{pdf_root}/{clean_filename}",
            ]
        )

        return list(dict.fromkeys(paths))

    def _pdf_lookup_paths_from_storage_path(self, path: str) -> list[str]:
        normalized = str(path or "").replace("\\", "/").strip("/")
        parts = normalized.split("/")

        if len(parts) < 4 or "pdf" not in parts:
            return []

        pdf_index = parts.index("pdf")

        if pdf_index < 1 or pdf_index + 1 >= len(parts):
            return []

        slug = parts[pdf_index - 1]
        filename = parts[-1]

        if "/" in filename or not filename.lower().endswith(".pdf"):
            return []

        return [lookup_path for lookup_path in self._pdf_lookup_paths(slug, filename) if lookup_path != normalized]

    def _extract_relative_path_from_href(self, href: str) -> str:
        parsed = urlparse(href)
        path = unquote(parsed.path)

        raw_prefix = f"/remote.php/dav/files/{self.username}/"
        encoded_prefix = f"/remote.php/dav/files/{quote(self.username)}/"

        if raw_prefix in path:
            return path.split(raw_prefix, 1)[1]

        if encoded_prefix in path:
            return path.split(encoded_prefix, 1)[1]

        return path.lstrip("/")

    def list_form_files(self) -> list[str]:
        self.ensure_base_structure()

        xml_body = """<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:">
  <d:prop>
    <d:resourcetype/>
    <d:getcontenttype/>
  </d:prop>
</d:propfind>"""

        response = self._request(
            "PROPFIND",
            self.forms_dir,
            headers={
                "Depth": "1",
                "Content-Type": "application/xml; charset=utf-8",
            },
            data=xml_body.encode("utf-8"),
        )

        self._check_status(response, (207,), f"Cannot list forms in '{self.forms_dir}'")

        ns = {"d": "DAV:"}
        root = ET.fromstring(response.text)

        files: list[str] = []

        for item in root.findall("d:response", ns):
            href = item.findtext("d:href", default="", namespaces=ns)
            relative_path = self._extract_relative_path_from_href(href)

            if not relative_path:
                continue

            path_obj = Path(relative_path)
            name = path_obj.name

            if path_obj.parent.as_posix().strip("/") != self.forms_dir.strip("/"):
                continue

            if name.endswith(".json"):
                files.append(name)

        return sorted(set(files))

    def read_form_json(self, filename: str) -> dict:
        response = self._request("GET", f"{self.forms_dir}/{filename}")
        self._check_status(response, (200,), f"Cannot read form '{filename}'")

        try:
            return json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise NextcloudStorageError(f"Invalid JSON in form '{filename}': {exc}") from exc

    def read_text_or_empty(self, path: str) -> str:
        response = self._request("GET", path)

        if response.status_code == 404:
            return ""

        self._check_status(response, (200,), f"Cannot read file '{path}'")
        return response.text

    def read_bytes(self, path: str) -> bytes:
        response = self._request("GET", path)
        self._check_status(response, (200,), f"Cannot read binary file '{path}'")
        return response.content

    def write_text(self, path: str, content: str, content_type: str) -> None:
        response = self._request(
            "PUT",
            path,
            headers={"Content-Type": content_type},
            data=content.encode("utf-8"),
        )

        self._check_status(response, (200, 201, 204), f"Cannot write file '{path}'")

    def write_bytes(self, path: str, content: bytes, content_type: str) -> None:
        response = self._request(
            "PUT",
            path,
            headers={"Content-Type": content_type},
            data=content,
        )

        self._check_status(response, (200, 201, 204), f"Cannot write file '{path}'")

    def append_csv_row(self, slug: str, row: dict) -> None:
        self.ensure_form_output_structure(slug)

        csv_path = self._csv_path(slug)
        existing = self.read_text_or_empty(csv_path)

        new_fieldnames = list(row.keys())
        buffer = io.StringIO()

        if existing.strip():
            existing_reader = csv.DictReader(io.StringIO(existing))
            existing_fieldnames = existing_reader.fieldnames or []

            merged_fieldnames = list(existing_fieldnames)

            for key in new_fieldnames:
                if key not in merged_fieldnames:
                    merged_fieldnames.append(key)

            all_rows = []

            for existing_row in existing_reader:
                all_rows.append(
                    {key: existing_row.get(key, "") for key in merged_fieldnames}
                )

            all_rows.append(
                {key: row.get(key, "") for key in merged_fieldnames}
            )

            writer = csv.DictWriter(buffer, fieldnames=merged_fieldnames)
            writer.writeheader()

            for item in all_rows:
                writer.writerow(item)

        else:
            writer = csv.DictWriter(buffer, fieldnames=new_fieldnames)
            writer.writeheader()
            writer.writerow(row)

        self.write_text(csv_path, buffer.getvalue(), "text/csv; charset=utf-8")

    def read_csv_rows(self, slug: str) -> list[dict]:
        existing = self.read_text_or_empty(self._csv_path(slug))

        if not existing.strip():
            return []

        reader = csv.DictReader(io.StringIO(existing))
        return list(reader)

    def save_pdf(
        self,
        slug: str,
        filename: str,
        pdf_bytes: bytes,
        document_type: str | None = None,
        signed: bool | None = None,
    ) -> None:
        self.ensure_form_output_structure(slug)

        resolved_document_type = document_type or self._infer_pdf_document_type_from_filename(filename)
        resolved_signed = self._infer_pdf_signed_from_filename(filename) if signed is None else signed

        pdf_directory = self._pdf_directory(
            slug=slug,
            document_type=resolved_document_type,
            signed=resolved_signed,
        )

        self.write_bytes(
            f"{pdf_directory}/{filename}",
            pdf_bytes,
            "application/pdf",
        )

    def save_declaration_pdf(
        self,
        slug: str,
        filename: str,
        pdf_bytes: bytes,
        signed: bool = False,
    ) -> None:
        self.save_pdf(
            slug=slug,
            filename=filename,
            pdf_bytes=pdf_bytes,
            document_type=self.PDF_DECLARATION_DIR,
            signed=signed,
        )

    def save_agreement_pdf(
        self,
        slug: str,
        filename: str,
        pdf_bytes: bytes,
        signed: bool = False,
    ) -> None:
        self.save_pdf(
            slug=slug,
            filename=filename,
            pdf_bytes=pdf_bytes,
            document_type=self.PDF_AGREEMENT_DIR,
            signed=signed,
        )

    def get_pdf_bytes(self, slug: str, filename: str) -> bytes:
        last_error: Exception | None = None

        for path in self._pdf_lookup_paths(slug, filename):
            try:
                return self.read_bytes(path)
            except NextcloudStorageError as exc:
                last_error = exc

        if last_error:
            raise last_error

        raise NextcloudStorageError(f"Cannot read PDF file '{filename}'")

    def get_file_bytes(self, path: str) -> bytes:
        normalized_path = str(path).replace("\\", "/").lstrip("/")
        return self.read_bytes(normalized_path)

    def ensure_outputs_for_all_forms(self) -> None:
        for filename in self.list_form_files():
            slug = Path(filename).stem
            self.ensure_form_output_structure(slug)

    def update_csv_row_by_submission_id(
        self,
        slug: str,
        submission_id: str,
        updates: dict,
    ) -> bool:
        csv_path = self._csv_path(slug)
        existing = self.read_text_or_empty(csv_path)

        if not existing.strip():
            return False

        reader = csv.DictReader(io.StringIO(existing))
        fieldnames = list(reader.fieldnames or [])

        for key in updates.keys():
            if key not in fieldnames:
                fieldnames.append(key)

        rows = []
        found = False

        for row in reader:
            if row.get("submission_id", "").strip() == submission_id:
                row.update(updates)
                found = True

            rows.append({key: row.get(key, "") for key in fieldnames})

        if not found:
            return False

        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

        self.write_text(csv_path, buffer.getvalue(), "text/csv; charset=utf-8")
        return True


def create_nextcloud_storage_from_env() -> NextcloudStorage:
    ca_bundle = os.environ.get("NEXTCLOUD_CA_BUNDLE", "").strip()
    verify_ssl_raw = os.environ.get("NEXTCLOUD_VERIFY_SSL", "true").strip()

    verify_ssl: Union[bool, str]

    if ca_bundle:
        verify_ssl = ca_bundle
    else:
        verify_ssl = verify_ssl_raw

    return NextcloudStorage(
        base_url=os.environ["NEXTCLOUD_BASE_URL"],
        username=os.environ["NEXTCLOUD_USERNAME"],
        app_password=os.environ["NEXTCLOUD_APP_PASSWORD"],
        forms_dir=os.environ.get("NEXTCLOUD_FORMS_DIR", "Formularze"),
        output_dir=os.environ.get("NEXTCLOUD_OUTPUT_DIR", "output"),
        csv_filename=os.environ.get("CSV_FILENAME", "dane.csv"),
        verify_ssl=verify_ssl,
    )
