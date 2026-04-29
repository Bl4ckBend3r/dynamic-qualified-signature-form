from pathlib import Path


MOBYWATEL_SIGNATURE_MARKERS = (
    b"mObywatel",
    b"mobYwatel",
    b"/Type /Sig",
    b"/SubFilter /adbe.pkcs7.detached",
    b"/SubFilter /ETSI.CAdES.detached",
)


def is_pdf_signed_by_mobywatel(pdf_path: str | Path) -> bool:
    path = Path(pdf_path)

    if not path.exists() or not path.is_file():
        return False

    content = path.read_bytes()

    has_signature_object = b"/Type /Sig" in content
    has_mobywatel_marker = any(
        marker.lower() in content.lower()
        for marker in MOBYWATEL_SIGNATURE_MARKERS
    )

    return has_signature_object and has_mobywatel_marker