from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import hmac
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

from asn1crypto import cms
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import rsa
from pypdf import PdfReader


SUPPORTED_FILTER = "/Adobe.PPKLite"
SUPPORTED_SUBFILTER = "/ETSI.CAdES.detached"
SUPPORTED_DIGEST = "sha256"
SUPPORTED_SIGNATURE_ALGORITHMS = {"sha256_rsa", "rsassa_pkcs1v15"}

_SHA256_DIGEST_INFO_WITH_NULL = bytes.fromhex(
    "3031300d060960864801650304020105000420"
)
_SHA256_DIGEST_INFO_WITHOUT_NULL = bytes.fromhex(
    "302f300b06096086480165030402010420"
)


class TrustedProfileVerificationError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class PdfCmsSignature:
    contents: bytes
    byte_range: tuple[int, int, int, int]
    filter_name: str
    subfilter: str


def _extract_pdf_signatures(pdf_bytes: bytes) -> list[PdfCmsSignature]:
    """Read signature dictionaries through pypdf, without scanning PDF syntax."""
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        root = reader.trailer["/Root"]
        acro_form = root.get("/AcroForm")
        if not acro_form:
            return []
        fields = acro_form.get_object().get("/Fields", [])
    except Exception as exc:
        raise TrustedProfileVerificationError("VERIFICATION_ERROR") from exc

    signatures: list[PdfCmsSignature] = []
    pending = [(field_ref, None) for field_ref in fields]
    visited: set[tuple[int, int] | int] = set()
    while pending:
        field_ref, inherited_type = pending.pop(0)
        try:
            field = field_ref.get_object()
            identity = (
                (field_ref.idnum, field_ref.generation)
                if hasattr(field_ref, "idnum")
                else id(field)
            )
            if identity in visited:
                continue
            visited.add(identity)
            field_type = field.get("/FT", inherited_type)
            pending.extend((child, field_type) for child in field.get("/Kids", []))
            if field_type != "/Sig" or not field.get("/V"):
                continue
            value = field["/V"].get_object()
            contents = value.get("/Contents")
            byte_range = value.get("/ByteRange")
            if contents is None or byte_range is None or len(byte_range) != 4:
                raise TrustedProfileVerificationError("UNSUPPORTED_SIGNATURE_FORMAT")
            signatures.append(
                PdfCmsSignature(
                    contents=bytes(contents),
                    byte_range=tuple(int(part) for part in byte_range),
                    filter_name=str(value.get("/Filter") or ""),
                    subfilter=str(value.get("/SubFilter") or ""),
                )
            )
        except TrustedProfileVerificationError:
            raise
        except Exception as exc:
            raise TrustedProfileVerificationError("VERIFICATION_ERROR") from exc
    return signatures


def _signed_pdf_data(pdf_bytes: bytes, byte_range: tuple[int, int, int, int]) -> bytes:
    first_start, first_length, second_start, second_length = byte_range
    values = (first_start, first_length, second_start, second_length)
    if any(value < 0 for value in values):
        raise TrustedProfileVerificationError("UNSUPPORTED_SIGNATURE_FORMAT")
    first_end = first_start + first_length
    second_end = second_start + second_length
    if (
        first_start != 0
        or first_end > second_start
        or second_start > len(pdf_bytes)
        or second_end != len(pdf_bytes)
    ):
        raise TrustedProfileVerificationError("DOCUMENT_INTEGRITY_FAILURE")
    return pdf_bytes[first_start:first_end] + pdf_bytes[second_start:second_end]


def _load_signed_data(contents: bytes) -> cms.SignedData:
    try:
        content_info = cms.ContentInfo.load(contents)
        if content_info["content_type"].native != "signed_data":
            raise TrustedProfileVerificationError("UNSUPPORTED_SIGNATURE_FORMAT")
        signed_data = content_info["content"]
        if signed_data["encap_content_info"]["content"].native is not None:
            raise TrustedProfileVerificationError("UNSUPPORTED_SIGNATURE_FORMAT")
        if len(signed_data["signer_infos"]) != 1:
            raise TrustedProfileVerificationError("UNSUPPORTED_SIGNATURE_FORMAT")
        return signed_data
    except TrustedProfileVerificationError:
        raise
    except Exception as exc:
        raise TrustedProfileVerificationError("UNSUPPORTED_SIGNATURE_FORMAT") from exc


def _certificate_ski(certificate: Any) -> bytes | None:
    extensions = certificate["tbs_certificate"]["extensions"]
    if extensions.native is None:
        return None
    for extension in extensions:
        if extension["extn_id"].native == "key_identifier":
            return extension["extn_value"].native
    return None


def _find_signer_certificate(signed_data: cms.SignedData, signer_info: Any) -> Any:
    certificates = [
        choice.chosen
        for choice in signed_data["certificates"]
        if choice.name == "certificate"
    ]
    sid = signer_info["sid"]
    if sid.name == "issuer_and_serial_number":
        wanted = sid.chosen
        for certificate in certificates:
            if (
                certificate.serial_number == wanted["serial_number"].native
                and certificate.issuer.dump() == wanted["issuer"].dump()
            ):
                return certificate
    elif sid.name == "subject_key_identifier":
        wanted_ski = sid.chosen.native
        for certificate in certificates:
            if _certificate_ski(certificate) == wanted_ski:
                return certificate
    raise TrustedProfileVerificationError("SIGNER_CERTIFICATE_NOT_FOUND")


def inspect_pdf_cms_signer_names(pdf_bytes: bytes) -> list[tuple[str, str]]:
    """Return CMS signer subject/issuer text for backend routing only."""
    names: list[tuple[str, str]] = []
    for signature in _extract_pdf_signatures(pdf_bytes):
        signed_data = _load_signed_data(signature.contents)
        signer = _find_signer_certificate(signed_data, signed_data["signer_infos"][0])
        subject = " | ".join(str(value) for value in signer.subject.native.values() if value)
        issuer = " | ".join(str(value) for value in signer.issuer.native.values() if value)
        names.append((subject, issuer))
    return names


def _signed_attributes_der(signer_info: Any) -> tuple[bytes, bytes]:
    signed_attrs = signer_info["signed_attrs"]
    if signed_attrs.native is None:
        raise TrustedProfileVerificationError("UNSUPPORTED_SIGNATURE_FORMAT")
    message_digests = [
        attribute["values"][0].native
        for attribute in signed_attrs
        if attribute["type"].native == "message_digest" and len(attribute["values"]) == 1
    ]
    if len(message_digests) != 1:
        raise TrustedProfileVerificationError("DOCUMENT_INTEGRITY_FAILURE")
    # RFC 5652 signs the DER SET OF value, not the context-specific [0] tag.
    return signed_attrs.untag().dump(force=True), message_digests[0]


def _validate_pkcs1_v15_block(encoded_message: bytes, digest: bytes) -> bool:
    if not encoded_message.startswith(b"\x00\x01"):
        return False
    separator = encoded_message.find(b"\x00", 2)
    if separator < 0:
        return False
    padding = encoded_message[2:separator]
    if len(padding) < 8 or any(value != 0xFF for value in padding):
        return False
    digest_info = encoded_message[separator + 1 :]
    expected = (
        _SHA256_DIGEST_INFO_WITH_NULL + digest,
        _SHA256_DIGEST_INFO_WITHOUT_NULL + digest,
    )
    return any(hmac.compare_digest(digest_info, candidate) for candidate in expected)


def _verify_rsa_signature(certificate: x509.Certificate, signature: bytes, data: bytes) -> bool:
    public_key = certificate.public_key()
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise TrustedProfileVerificationError("UNSUPPORTED_SIGNATURE_ALGORITHM")
    numbers = public_key.public_numbers()
    width = (public_key.key_size + 7) // 8
    if len(signature) != width:
        return False
    signature_number = int.from_bytes(signature, "big")
    if signature_number >= numbers.n:
        return False
    encoded_message = pow(signature_number, numbers.e, numbers.n).to_bytes(width, "big")
    return _validate_pkcs1_v15_block(encoded_message, sha256(data).digest())


def _extension(certificate: x509.Certificate, extension_type: Any) -> Any | None:
    try:
        return certificate.extensions.get_extension_for_class(extension_type).value
    except x509.ExtensionNotFound:
        return None


def _check_certificate_time(certificate: x509.Certificate, moment: datetime) -> None:
    if not (certificate.not_valid_before_utc <= moment <= certificate.not_valid_after_utc):
        raise TrustedProfileVerificationError("CERTIFICATE_EXPIRED")


def _check_usage(certificate: x509.Certificate, *, ca: bool) -> None:
    constraints = _extension(certificate, x509.BasicConstraints)
    if constraints is None or constraints.ca is not ca:
        raise TrustedProfileVerificationError("CERTIFICATE_TRUST_FAILURE")
    usage = _extension(certificate, x509.KeyUsage)
    if usage is None:
        raise TrustedProfileVerificationError("CERTIFICATE_TRUST_FAILURE")
    if ca and not usage.key_cert_sign:
        raise TrustedProfileVerificationError("CERTIFICATE_TRUST_FAILURE")
    if not ca and not (usage.digital_signature or usage.content_commitment):
        raise TrustedProfileVerificationError("CERTIFICATE_TRUST_FAILURE")


def _aki_ski_match(child: x509.Certificate, issuer: x509.Certificate) -> bool:
    authority = _extension(child, x509.AuthorityKeyIdentifier)
    subject = _extension(issuer, x509.SubjectKeyIdentifier)
    if authority is None or authority.key_identifier is None or subject is None:
        return True
    return hmac.compare_digest(authority.key_identifier, subject.digest)


def _certificate_signature_valid(child: x509.Certificate, issuer: x509.Certificate) -> bool:
    if child.issuer != issuer.subject or not _aki_ski_match(child, issuer):
        return False
    public_key = issuer.public_key()
    if not isinstance(public_key, rsa.RSAPublicKey):
        return False
    parameters = child.signature_algorithm_parameters
    if parameters is None or child.signature_hash_algorithm is None:
        return False
    try:
        public_key.verify(
            child.signature,
            child.tbs_certificate_bytes,
            parameters,
            child.signature_hash_algorithm,
        )
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def _load_certificates(paths: Iterable[Path]) -> list[x509.Certificate]:
    certificates = []
    for path in paths:
        try:
            data = path.read_bytes()
            certificates.append(
                x509.load_pem_x509_certificate(data)
                if b"-----BEGIN CERTIFICATE-----" in data
                else x509.load_der_x509_certificate(data)
            )
        except Exception as exc:
            raise TrustedProfileVerificationError("CERTIFICATE_TRUST_FAILURE") from exc
    return certificates


def _verify_local_chain(
    signer: x509.Certificate,
    intermediate_paths: Iterable[Path],
    root_paths: Iterable[Path],
    *,
    moment: datetime,
) -> None:
    intermediates = _load_certificates(intermediate_paths)
    roots = _load_certificates(root_paths)
    if not intermediates or not roots:
        raise TrustedProfileVerificationError("CERTIFICATE_TRUST_FAILURE")

    _check_usage(signer, ca=False)
    _check_certificate_time(signer, moment)
    for intermediate in intermediates:
        if not _certificate_signature_valid(signer, intermediate):
            continue
        _check_usage(intermediate, ca=True)
        _check_certificate_time(intermediate, moment)
        for root in roots:
            if not _certificate_signature_valid(intermediate, root):
                continue
            _check_usage(root, ca=True)
            _check_certificate_time(root, moment)
            if root.subject != root.issuer or not _certificate_signature_valid(root, root):
                continue
            return
    raise TrustedProfileVerificationError("CERTIFICATE_TRUST_FAILURE")


class TrustedProfileMinimalBackend:
    name = "trusted_profile_minimal_cms"

    def __init__(self, *, trust_roots: Iterable[Path], intermediates: Iterable[Path]) -> None:
        self._trust_roots = tuple(trust_roots)
        self._intermediates = tuple(intermediates)

    def verify(self, pdf_bytes: bytes) -> dict[str, Any]:
        result: dict[str, Any] = {
            "valid": False,
            "signature_count": 0,
            "cryptographic_valid": None,
            "document_integrity_valid": None,
            "certificate_chain_valid": None,
            "signer_certificate_found": False,
            "timestamp_valid": None,
            "revocation_status": "NOT_CHECKED",
            "digest_algorithm": None,
            "signature_algorithm": None,
            "reason_code": "VERIFICATION_ERROR",
            "trust_roots_loaded_count": len(self._trust_roots),
            "intermediate_certs_loaded_count": len(self._intermediates),
        }
        try:
            signatures = _extract_pdf_signatures(pdf_bytes)
            result["signature_count"] = len(signatures)
            if not signatures:
                raise TrustedProfileVerificationError("NO_SIGNATURE")
            if len(signatures) != 1:
                raise TrustedProfileVerificationError("UNSUPPORTED_SIGNATURE_FORMAT")
            signature = signatures[0]
            if signature.filter_name != SUPPORTED_FILTER or signature.subfilter != SUPPORTED_SUBFILTER:
                raise TrustedProfileVerificationError("UNSUPPORTED_SIGNATURE_FORMAT")

            signed_pdf_data = _signed_pdf_data(pdf_bytes, signature.byte_range)
            signed_data = _load_signed_data(signature.contents)
            signer_info = signed_data["signer_infos"][0]
            digest_algorithm = signer_info["digest_algorithm"]["algorithm"].native
            signature_algorithm = signer_info["signature_algorithm"]["algorithm"].native
            result["digest_algorithm"] = digest_algorithm
            result["signature_algorithm"] = signature_algorithm
            if digest_algorithm != SUPPORTED_DIGEST or signature_algorithm not in SUPPORTED_SIGNATURE_ALGORITHMS:
                raise TrustedProfileVerificationError("UNSUPPORTED_SIGNATURE_ALGORITHM")

            signer_asn1 = _find_signer_certificate(signed_data, signer_info)
            result["signer_certificate_found"] = True
            signed_attrs_der, embedded_digest = _signed_attributes_der(signer_info)
            result["document_integrity_valid"] = hmac.compare_digest(
                sha256(signed_pdf_data).digest(), embedded_digest
            )
            if not result["document_integrity_valid"]:
                raise TrustedProfileVerificationError("DOCUMENT_INTEGRITY_FAILURE")

            signer_certificate = x509.load_der_x509_certificate(signer_asn1.dump())
            result["cryptographic_valid"] = _verify_rsa_signature(
                signer_certificate,
                signer_info["signature"].native,
                signed_attrs_der,
            )
            if not result["cryptographic_valid"]:
                raise TrustedProfileVerificationError("SIGNATURE_CRYPTOGRAPHIC_FAILURE")

            _verify_local_chain(
                signer_certificate,
                self._intermediates,
                self._trust_roots,
                moment=datetime.now(timezone.utc),
            )
            result["certificate_chain_valid"] = True
            result["valid"] = True
            result["reason_code"] = "VALID_SIGNATURE"
        except TrustedProfileVerificationError as exc:
            result["reason_code"] = exc.reason_code
            if exc.reason_code in {"CERTIFICATE_TRUST_FAILURE", "CERTIFICATE_EXPIRED"}:
                result["certificate_chain_valid"] = False
            if exc.reason_code == "SIGNATURE_CRYPTOGRAPHIC_FAILURE":
                result["cryptographic_valid"] = False
            if exc.reason_code == "DOCUMENT_INTEGRITY_FAILURE":
                result["document_integrity_valid"] = False
        except Exception:
            result["reason_code"] = "VERIFICATION_ERROR"
        return result
