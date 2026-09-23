from __future__ import annotations

import re
from pathlib import PurePath


PDF_MIME_TYPES = {"application/pdf", "application/x-pdf"}
DOCUMENT_MIME_TYPES = {
    "application/pdf",
    "application/x-pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
FORM_IMPORT_INSTRUCTION_MIME_TYPES = {
    "application/pdf",
    "application/x-pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/html",
    "text/markdown",
    "text/plain",
}
LOGO_SIGNATURES = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (b"RIFF",),
}
SVG_MIME_TYPES = {"image/svg+xml"}
MAX_PDF_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_DOCUMENT_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_LOGO_UPLOAD_BYTES = 5 * 1024 * 1024
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
SAFE_ATTACHMENT_TYPES = {
    "pdf": ("application/pdf", (b"%PDF",)),
    "doc": ("application/msword", (b"\xd0\xcf\x11\xe0",)),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", (b"PK",)),
    "xls": ("application/vnd.ms-excel", (b"\xd0\xcf\x11\xe0",)),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", (b"PK",)),
    "png": ("image/png", (b"\x89PNG\r\n\x1a\n",)),
    "jpg": ("image/jpeg", (b"\xff\xd8\xff",)),
    "jpeg": ("image/jpeg", (b"\xff\xd8\xff",)),
}
SAFE_ATTACHMENT_EXTENSIONS = frozenset(SAFE_ATTACHMENT_TYPES)
SAFE_ATTACHMENT_MIME_TYPES = frozenset(
    {item[0] for item in SAFE_ATTACHMENT_TYPES.values()} | {"application/x-pdf"}
)


class UploadValidationError(ValueError):
    pass


def validate_attachment_upload(
    filename: str,
    content: bytes,
    mime_type: str | None,
    *,
    allowed_extensions: list[str],
    allowed_mime_types: list[str] | None = None,
    max_size_bytes: int,
) -> tuple[str, str]:
    extensions = {str(item).lower().lstrip(".") for item in allowed_extensions}
    if not extensions or not extensions.issubset(SAFE_ATTACHMENT_EXTENSIONS):
        raise UploadValidationError("Konfiguracja zawiera niedozwolone rozszerzenie pliku.")
    clean_name = validate_upload_filename(filename, allowed_suffixes={f".{item}" for item in extensions})
    if not content:
        raise UploadValidationError("Plik jest pusty.")
    if len(content) > max_size_bytes:
        raise UploadValidationError("Plik jest zbyt duzy.")
    extension = PurePath(clean_name).suffix.lower().lstrip(".")
    detected_mime, signatures = SAFE_ATTACHMENT_TYPES[extension]
    if not any(content.startswith(signature) for signature in signatures):
        raise UploadValidationError("Zawartosc pliku nie zgadza sie z jego rozszerzeniem.")
    client_mime = str(mime_type or "").split(";", 1)[0].strip().lower()
    compatible = {detected_mime}
    if detected_mime == "application/pdf":
        compatible.add("application/x-pdf")
    if client_mime and client_mime not in compatible:
        raise UploadValidationError("Typ MIME nie zgadza sie z zawartoscia pliku.")
    configured_mimes = {str(item).lower() for item in (allowed_mime_types or [])}
    if configured_mimes and not compatible.intersection(configured_mimes):
        raise UploadValidationError("Typ MIME pliku nie jest dozwolony dla tego pola.")
    return clean_name, detected_mime


def validate_upload_filename(filename: str, *, allowed_suffixes: set[str]) -> str:
    clean_name = PurePath(str(filename or "").replace("\\", "/")).name
    if not clean_name or clean_name != str(filename or "").replace("\\", "/"):
        raise UploadValidationError("Nazwa pliku nie moze zawierac sciezki.")
    if CONTROL_CHARS.search(clean_name) or ".." in clean_name:
        raise UploadValidationError("Nazwa pliku zawiera niedozwolone znaki.")
    if PurePath(clean_name).suffix.lower() not in allowed_suffixes:
        raise UploadValidationError("Niedozwolone rozszerzenie pliku.")
    return clean_name


def validate_pdf_upload(filename: str, content: bytes, mime_type: str | None = None) -> None:
    validate_upload_filename(filename, allowed_suffixes={".pdf"})
    if not content:
        raise UploadValidationError("Plik PDF jest pusty.")
    if len(content) > MAX_PDF_UPLOAD_BYTES:
        raise UploadValidationError("Plik PDF jest zbyt duzy.")
    if mime_type and mime_type not in PDF_MIME_TYPES:
        raise UploadValidationError("Nieprawidlowy typ MIME pliku PDF.")
    if not content.startswith(b"%PDF"):
        raise UploadValidationError("Plik nie ma poprawnego naglowka PDF.")


def validate_document_upload(filename: str, content: bytes, mime_type: str | None = None) -> str:
    clean_name = validate_upload_filename(filename, allowed_suffixes={".pdf", ".doc", ".docx"})
    if not content:
        raise UploadValidationError("Plik dokumentu jest pusty.")
    if len(content) > MAX_DOCUMENT_UPLOAD_BYTES:
        raise UploadValidationError("Plik dokumentu jest zbyt duży.")
    suffix = PurePath(clean_name).suffix.lower()
    normalized_mime = str(mime_type or "").strip().lower()
    expected_mime = {
        ".pdf": "application/pdf",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }[suffix]
    if normalized_mime and normalized_mime not in DOCUMENT_MIME_TYPES:
        raise UploadValidationError("Nieprawidłowy typ MIME dokumentu.")
    if suffix == ".pdf" and not content.startswith(b"%PDF"):
        raise UploadValidationError("Plik PDF nie ma poprawnego nagłówka.")
    if suffix == ".docx" and not content.startswith(b"PK"):
        raise UploadValidationError("Plik DOCX nie ma poprawnego nagłówka.")
    if suffix == ".doc" and not content.startswith(b"\xd0\xcf\x11\xe0"):
        raise UploadValidationError("Plik DOC nie ma poprawnego nagłówka.")
    if normalized_mime and normalized_mime != expected_mime:
        if not (suffix == ".pdf" and normalized_mime == "application/x-pdf"):
            raise UploadValidationError("Rozszerzenie lub MIME nie zgadza się z zawartością pliku.")
    return expected_mime


def validate_form_import_instruction_upload(
    filename: str,
    content: bytes,
    mime_type: str | None = None,
) -> str:
    clean_name = validate_upload_filename(filename, allowed_suffixes={".pdf", ".docx", ".html", ".md"})
    if not content:
        raise UploadValidationError("Plik instrukcji jest pusty.")
    if len(content) > MAX_DOCUMENT_UPLOAD_BYTES:
        raise UploadValidationError("Plik instrukcji jest zbyt duży.")
    suffix = PurePath(clean_name).suffix.lower()
    expected_mime = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".html": "text/html",
        ".md": "text/markdown",
    }[suffix]
    normalized_mime = str(mime_type or "").strip().lower()
    if normalized_mime and normalized_mime not in FORM_IMPORT_INSTRUCTION_MIME_TYPES:
        raise UploadValidationError("Nieprawidłowy typ MIME pliku instrukcji.")
    if suffix == ".pdf" and not content.startswith(b"%PDF"):
        raise UploadValidationError("Plik PDF nie ma poprawnego nagłówka.")
    if suffix == ".docx" and not content.startswith(b"PK"):
        raise UploadValidationError("Plik DOCX nie ma poprawnego nagłówka.")
    if suffix in {".html", ".md"}:
        try:
            content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise UploadValidationError("Plik tekstowy instrukcji musi byc zapisany w UTF-8.") from exc
    if normalized_mime and suffix in {".pdf", ".docx"} and normalized_mime != expected_mime:
        if not (suffix == ".pdf" and normalized_mime == "application/x-pdf"):
            raise UploadValidationError("Rozszerzenie lub MIME nie zgadza się z zawartością pliku.")
    return expected_mime


def validate_logo_upload(filename: str, content: bytes, mime_type: str | None = None) -> str:
    clean_name = validate_upload_filename(filename, allowed_suffixes={".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"})
    if not content:
        raise UploadValidationError("Plik logo jest pusty.")
    if len(content) > MAX_LOGO_UPLOAD_BYTES:
        raise UploadValidationError("Plik logo jest zbyt duzy.")
    normalized_mime = str(mime_type or "").strip().lower()
    if normalized_mime in SVG_MIME_TYPES or clean_name.lower().endswith(".svg"):
        _validate_svg_logo(content)
        return "image/svg+xml"
    if normalized_mime and normalized_mime not in LOGO_SIGNATURES:
        raise UploadValidationError("Nieprawidlowy typ MIME logo.")
    detected_mime = _detect_binary_logo_mime(content)
    if not detected_mime:
        raise UploadValidationError("Plik nie wyglada na obslugiwany obraz.")
    if normalized_mime and normalized_mime != detected_mime:
        if not (normalized_mime == "image/jpeg" and detected_mime == "image/jpeg"):
            raise UploadValidationError("Rozszerzenie lub MIME nie zgadza sie z zawartoscia pliku.")
    return detected_mime


def _detect_binary_logo_mime(content: bytes) -> str:
    for mime_type, signatures in LOGO_SIGNATURES.items():
        if any(content.startswith(signature) for signature in signatures):
            if mime_type == "image/webp" and content[8:12] != b"WEBP":
                continue
            return mime_type
    return ""


def _validate_svg_logo(content: bytes) -> None:
    text = content[:200_000].decode("utf-8", errors="ignore").lower()
    if "<svg" not in text:
        raise UploadValidationError("Plik SVG nie zawiera elementu svg.")
    if "<script" in text or "javascript:" in text or re.search(r"\son[a-z]+\s*=", text):
        raise UploadValidationError("Plik SVG zawiera niedozwolony kod.")
