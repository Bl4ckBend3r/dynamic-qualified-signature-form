import csv
import io
import json
import logging
import os
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
import xml.etree.ElementTree as ET

import requests

logger = logging.getLogger(__name__)


class NextcloudStorageError(Exception):
    pass


class NextcloudStorage:
    def __init__(
        self,
        base_url: str,
        username: str,
        app_password: str,
        forms_dir: str = "Formularze",
        output_dir: str = "output",
        timeout: int = 30,
        verify_ssl: bool | str = True,
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
        self.timeout = timeout
        self.verify_ssl = verify_ssl

        self.auth = (self.username, self.app_password)
        self.dav_root = f"{self.base_url}/remote.php/dav/files/{quote(self.username)}"

        logger.info("Nextcloud verify_ssl=%r", self.verify_ssl)
        logger.info("Nextcloud DAV root=%s", self.dav_root)

    def _encode_path(self, path: str) -> str:
        clean = path.strip("/")
        if not clean:
            return self.dav_root
        encoded = "/".join(quote(part) for part in clean.split("/"))
        return f"{self.dav_root}/{encoded}"

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = self._encode_path(path)
        try:
            return requests.request(
                method=method,
                url=url,
                auth=self.auth,
                timeout=self.timeout,
                verify=self.verify_ssl,
                **kwargs,
            )
        except requests.exceptions.SSLError as exc:
            raise NextcloudStorageError(
                f"SSL error for URL '{url}'. "
                f"Check certificate chain or configure NEXTCLOUD_CA_BUNDLE / NEXTCLOUD_VERIFY_SSL. "
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
        return response.status_code in (200, 207)

    def mkdir(self, path: str) -> None:
        if self.exists(path):
            return

        response = self._request("MKCOL", path)
        if response.status_code in (201, 405):
            return

        raise NextcloudStorageError(
            f"Cannot create directory '{path}'. HTTP {response.status_code}. Response: {response.text}"
        )

    def ensure_base_structure(self) -> None:
        self.mkdir(self.forms_dir)
        self.mkdir(self.output_dir)

    def ensure_form_output_structure(self, slug: str) -> None:
        self.mkdir(f"{self.output_dir}/{slug}")
        self.mkdir(f"{self.output_dir}/{slug}/pdf")

    def _extract_relative_path_from_href(self, href: str) -> str:
        parsed = urlparse(href)
        path = unquote(parsed.path)

        dav_prefix = f"/remote.php/dav/files/{self.username}/"
        if dav_prefix in path:
            return path.split(dav_prefix, 1)[1]

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

        csv_path = f"{self.output_dir}/{slug}/data.csv"
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
                all_rows.append({k: existing_row.get(k, "") for k in merged_fieldnames})

            all_rows.append({k: row.get(k, "") for k in merged_fieldnames})

            writer = csv.DictWriter(buffer, fieldnames=merged_fieldnames)
            writer.writeheader()
            for item in all_rows:
                writer.writerow(item)
        else:
            writer = csv.DictWriter(buffer, fieldnames=new_fieldnames)
            writer.writeheader()
            writer.writerow(row)

        self.write_text(csv_path, buffer.getvalue(), "text/csv; charset=utf-8")

    def save_pdf(self, slug: str, filename: str, pdf_bytes: bytes) -> None:
        self.ensure_form_output_structure(slug)
        self.write_bytes(
            f"{self.output_dir}/{slug}/pdf/{filename}",
            pdf_bytes,
            "application/pdf",
        )

    def get_pdf_bytes(self, slug: str, filename: str) -> bytes:
        return self.read_bytes(f"{self.output_dir}/{slug}/pdf/{filename}")

    def ensure_outputs_for_all_forms(self) -> None:
        for filename in self.list_form_files():
            slug = Path(filename).stem
            self.ensure_form_output_structure(slug)


def create_nextcloud_storage_from_env() -> NextcloudStorage:
    ca_bundle = os.environ.get("NEXTCLOUD_CA_BUNDLE", "").strip()
    verify_ssl_flag = os.environ.get("NEXTCLOUD_VERIFY_SSL", "true").lower() == "true"

    verify_ssl: bool | str
    if ca_bundle:
        verify_ssl = ca_bundle
    else:
        verify_ssl = verify_ssl_flag

    return NextcloudStorage(
        base_url=os.environ["NEXTCLOUD_BASE_URL"],
        username=os.environ["NEXTCLOUD_USERNAME"],
        app_password=os.environ["NEXTCLOUD_APP_PASSWORD"],
        forms_dir=os.environ.get("NEXTCLOUD_FORMS_DIR", "Formularze"),
        output_dir=os.environ.get("NEXTCLOUD_OUTPUT_DIR", "output"),
        verify_ssl=verify_ssl,
    )