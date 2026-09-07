from signature_verifier import _build_default_result, _classify_signature, _match_any, verify_signed_pdf
import pytest


@pytest.fixture
def certificate_chain_signed_pdf_factory(tmp_path, monkeypatch):
    """Create a real root -> intermediate -> signer chain for pyHanko tests."""
    from datetime import datetime, timedelta, timezone
    from io import BytesIO
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from asn1crypto import keys, x509 as asn1_x509
    from pyhanko.sign import signers
    from pyhanko.sign.fields import SigSeedSubFilter
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko_certvalidator.registry import SimpleCertificateStore
    from pypdf import PdfWriter

    now = datetime.now(timezone.utc)

    def name(common_name):
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])

    def issue(subject, issuer, public_key, issuer_key, *, ca):
        builder = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(public_key)
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=not ca,
                    content_commitment=not ca,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=ca,
                    crl_sign=ca,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
        )
        return builder.sign(issuer_key, hashes.SHA256())

    root_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    root_name = name("TEST NCC Root")
    root_cert = issue(root_name, root_name, root_key.public_key(), root_key, ca=True)
    intermediate_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    intermediate_name = name("TEST Centrum Kwalifikowane EuroCert")
    intermediate_cert = issue(
        intermediate_name,
        root_name,
        intermediate_key.public_key(),
        root_key,
        ca=True,
    )
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_name = name("Minister informatyzacji - pieczęć podpisu zaufanego")
    leaf_cert = issue(
        leaf_name,
        intermediate_name,
        leaf_key.public_key(),
        intermediate_key,
        ca=False,
    )
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    wrong_name = name("TEST Wrong Root")
    wrong_cert = issue(wrong_name, wrong_name, wrong_key.public_key(), wrong_key, ca=True)

    root_path = tmp_path / "root.pem"
    intermediate_path = tmp_path / "intermediate.cer"
    leaf_path = tmp_path / "leaf.crt"
    wrong_root_path = tmp_path / "wrong-root.pem"
    root_path.write_bytes(root_cert.public_bytes(serialization.Encoding.PEM))
    intermediate_path.write_bytes(intermediate_cert.public_bytes(serialization.Encoding.DER))
    leaf_path.write_bytes(leaf_cert.public_bytes(serialization.Encoding.PEM))
    wrong_root_path.write_bytes(wrong_cert.public_bytes(serialization.Encoding.PEM))

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    unsigned = BytesIO()
    writer.write(unsigned)
    registry = SimpleCertificateStore()
    signer = signers.SimpleSigner(
        signing_cert=asn1_x509.Certificate.load(leaf_cert.public_bytes(serialization.Encoding.DER)),
        signing_key=keys.PrivateKeyInfo.load(
            leaf_key.private_bytes(
                serialization.Encoding.DER,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        ),
        cert_registry=registry,
    )
    signed = signers.sign_pdf(
        IncrementalPdfFileWriter(BytesIO(unsigned.getvalue())),
        signers.PdfSignatureMetadata(field_name="Signature1", subfilter=SigSeedSubFilter.PADES),
        signer=signer,
    ).getvalue()
    signed_path = tmp_path / "chain-signed.pdf"
    signed_path.write_bytes(signed)
    monkeypatch.delenv("SIGNATURE_CRL_FILES", raising=False)
    monkeypatch.setenv("SIGNATURE_ALLOW_FETCHING", "false")
    return {
        "signed_path": signed_path,
        "root_path": root_path,
        "intermediate_path": intermediate_path,
        "leaf_path": leaf_path,
        "wrong_root_path": wrong_root_path,
    }


@pytest.fixture
def signed_pdf_factory(tmp_path, monkeypatch):
    """Real test-only certificate, explicitly trusted; no mocks of cryptography."""
    from datetime import datetime, timedelta, timezone
    from io import BytesIO
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    from asn1crypto import keys, x509 as asn1_x509
    from pyhanko.sign import signers
    from pyhanko.sign.fields import SigSeedSubFilter
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko_certvalidator.registry import SimpleCertificateStore

    trust_roots = []

    def sign(data, provider='mszafir', field_name='Signature1', key_kind='rsa'):
        key = (ec.generate_private_key(ec.SECP256R1()) if key_kind == 'ecdsa'
               else rsa.generate_private_key(public_exponent=65537, key_size=2048))
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,
            {'mszafir': 'TEST COPE SZAFIR', 'profil_zaufany': 'TEST Profil Zaufany', 'qualified_other': 'TEST EUROCERT'}[provider])])
        now = datetime.now(timezone.utc)
        certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False), critical=True).sign(key, hashes.SHA256()))
        pem_path = tmp_path / f'{field_name}-{provider}.pem'
        pem_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        trust_roots.append(str(pem_path))
        monkeypatch.setenv('SIGNATURE_TRUST_ROOTS', ';'.join(trust_roots))
        signer = signers.SimpleSigner(signing_cert=asn1_x509.Certificate.load(certificate.public_bytes(serialization.Encoding.DER)),
            signing_key=keys.PrivateKeyInfo.load(key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())),
            cert_registry=SimpleCertificateStore(), prefer_pss=key_kind == 'rsa_pss')
        output = signers.sign_pdf(IncrementalPdfFileWriter(BytesIO(data)),
            signers.PdfSignatureMetadata(field_name=field_name, subfilter=SigSeedSubFilter.PADES), signer=signer)
        return output.getvalue()
    return sign


@pytest.mark.parametrize('provider', ['mszafir'])
def test_real_signed_pdf_valid_and_tampered(signed_pdf_factory, tmp_path, provider):
    from io import BytesIO
    from pypdf import PdfWriter
    from signature_verifier import signature_verification_result
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_metadata({'/Title': 'Original document'})
    output = BytesIO()
    writer.write(output)
    signed = signed_pdf_factory(output.getvalue(), provider, field_name='Group.Signature1')
    path = tmp_path / 'signed.pdf'
    path.write_bytes(signed)
    result = verify_signed_pdf(path)
    assert result['validation_status'] == 'VALID', result
    assert result['cryptographic_valid'] is True
    assert result['document_integrity_valid'] is True
    assert result['certificate_chain_valid'] is True
    assert result['timestamp_valid'] is None
    assert result['timestamp_status'] == 'NOT_PRESENT'
    assert result['signatures'][0]['md_algorithm'] == 'sha256'
    assert result['signatures'][0]['pkcs7_signature_mechanism']
    assert result['signatures'][0]['signed_attrs_present'] is True
    assert result['signatures'][0]['message_digest_matches'] is True
    assert signature_verification_result(result, [provider]) == 'VALID_SIGNATURE'
    assert signature_verification_result(result, []) == 'SIGNATURE_TYPE_NOT_ALLOWED'
    # Alter a signed, same-length object without damaging the PDF parser.
    changed = signed.replace(b'/MediaBox [ 0.0 0.0 200 200 ]', b'/MediaBox [ 0.0 0.0 201 200 ]', 1)
    if changed == signed:
        changed = signed.replace(b'200', b'201', 1)
    assert changed != signed
    path.write_bytes(changed)
    result = verify_signed_pdf(path)
    assert signature_verification_result(result, [provider]) == 'INVALID_SIGNATURE', result
    assert result['reason_code'] == 'DOCUMENT_INTEGRITY_FAILURE'


def test_verifier_exception_is_technical_error(signed_pdf_factory, monkeypatch, tmp_path):
    from io import BytesIO
    from pypdf import PdfWriter
    from signature_verifier import signature_verification_result
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    data = BytesIO()
    writer.write(data)
    path = tmp_path / 'signed.pdf'
    path.write_bytes(signed_pdf_factory(data.getvalue()))
    def fail(*args, **kwargs):
        raise TimeoutError('Test verifier unavailable')
    monkeypatch.setattr('signature_verifier.validate_pdf_signature', fail)
    result = verify_signed_pdf(path)
    assert signature_verification_result(result, ['mszafir']) == 'VERIFICATION_ERROR'
    assert result['validation_status'] == 'ERROR'


def test_signed_pdf_without_trust_configuration_is_not_accepted(signed_pdf_factory, monkeypatch, tmp_path):
    from io import BytesIO
    from pypdf import PdfWriter
    from signature_verifier import signature_verification_result
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    output = BytesIO()
    writer.write(output)
    path = tmp_path / 'unknown-root.pdf'
    path.write_bytes(signed_pdf_factory(output.getvalue()))
    monkeypatch.delenv('SIGNATURE_TRUST_ROOTS')
    result = verify_signed_pdf(path)
    assert result['reason_code'] == 'CERTIFICATE_TRUST_CONFIGURATION_MISSING'
    assert result['cryptographic_valid'] is None
    assert result['certificate_chain_valid'] is None
    assert signature_verification_result(result, ['mszafir']) == 'VERIFICATION_ERROR'


def test_trust_store_failure_preserves_signature_detection(signed_pdf_factory, monkeypatch, tmp_path):
    from io import BytesIO
    from pypdf import PdfWriter
    from signature_verifier import signature_verification_result
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    output = BytesIO()
    writer.write(output)
    path = tmp_path / 'signed.pdf'
    path.write_bytes(signed_pdf_factory(output.getvalue()))

    def fail():
        raise OSError('Test trust store unavailable')

    monkeypatch.setattr('signature_verifier._validation_context', fail)
    result = verify_signed_pdf(path)
    assert result['is_signed'] and result['signature_count'] == 1
    assert result['reason_code'] == 'TRUST_CONTEXT_ERROR'
    assert signature_verification_result(result, ['mszafir']) == 'VERIFICATION_ERROR'


def test_validation_context_requires_explicit_roots(monkeypatch):
    import signature_verifier

    monkeypatch.delenv('SIGNATURE_TRUST_ROOTS', raising=False)
    monkeypatch.delenv('SIGNATURE_INTERMEDIATE_CERTS', raising=False)
    monkeypatch.delenv('SIGNATURE_CRL_FILES', raising=False)
    with pytest.raises(signature_verifier.SignatureTrustConfigurationError) as error:
        signature_verifier._validation_context()
    assert error.value.reason_code == 'CERTIFICATE_TRUST_CONFIGURATION_MISSING'


def test_certificate_loader_supports_pem_and_der(certificate_chain_signed_pdf_factory):
    from signature_verifier import load_x509_certificate

    material = certificate_chain_signed_pdf_factory
    assert load_x509_certificate(material['root_path']).subject
    assert load_x509_certificate(material['intermediate_path']).subject


def test_certificate_loader_reports_missing_file(tmp_path):
    from signature_verifier import SignatureTrustConfigurationError, load_x509_certificate

    with pytest.raises(SignatureTrustConfigurationError) as error:
        load_x509_certificate(tmp_path / 'missing-root.pem')
    assert error.value.reason_code == 'TRUST_CERTIFICATE_FILE_NOT_FOUND'


def test_validation_context_separates_roots_and_intermediates(
    certificate_chain_signed_pdf_factory, monkeypatch
):
    import signature_verifier

    material = certificate_chain_signed_pdf_factory
    captured = {}
    monkeypatch.setenv('SIGNATURE_TRUST_ROOTS', str(material['root_path']))
    monkeypatch.setenv('SIGNATURE_INTERMEDIATE_CERTS', str(material['intermediate_path']))

    class Context:
        pass

    def capture(**kwargs):
        captured.update(kwargs)
        return Context()

    monkeypatch.setattr(signature_verifier, 'ValidationContext', capture)
    context = signature_verifier._validation_context()

    assert len(captured['trust_roots']) == 1
    assert len(captured['other_certs']) == 1
    assert captured['allow_fetching'] is False
    assert captured['revocation_mode'] == 'hard-fail'
    assert context._signature_trust_diagnostics['trust_roots_loaded_count'] == 1
    assert context._signature_trust_diagnostics['intermediate_certs_loaded_count'] == 1


def test_root_intermediate_and_cms_leaf_build_valid_chain(
    certificate_chain_signed_pdf_factory, monkeypatch
):
    material = certificate_chain_signed_pdf_factory
    monkeypatch.setenv('SIGNATURE_TRUST_ROOTS', str(material['root_path']))
    monkeypatch.setenv('SIGNATURE_INTERMEDIATE_CERTS', str(material['intermediate_path']))

    result = verify_signed_pdf(material['signed_path'])

    assert result['document_integrity_valid'] is True
    assert result['cryptographic_valid'] is True
    assert result['chain_built'] is True
    assert result['certificate_chain_valid'] is True
    assert result['trust_roots_loaded_count'] == 1
    assert result['intermediate_certs_loaded_count'] == 1
    assert result['provider'] == 'eurocert'
    assert result['detected_signature_type'] == 'profil_zaufany'


def test_missing_intermediate_is_trust_failure_not_crypto_failure(
    certificate_chain_signed_pdf_factory, monkeypatch
):
    material = certificate_chain_signed_pdf_factory
    monkeypatch.setenv('SIGNATURE_TRUST_ROOTS', str(material['root_path']))
    monkeypatch.delenv('SIGNATURE_INTERMEDIATE_CERTS', raising=False)

    result = verify_signed_pdf(material['signed_path'])

    assert result['document_integrity_valid'] is True
    assert result['cryptographic_valid'] is True
    assert result['certificate_chain_valid'] is False
    assert result['chain_built'] is False
    assert result['reason_code'] == 'CERTIFICATE_TRUST_FAILURE'


def test_wrong_root_is_trust_failure_not_crypto_failure(
    certificate_chain_signed_pdf_factory, monkeypatch
):
    material = certificate_chain_signed_pdf_factory
    monkeypatch.setenv('SIGNATURE_TRUST_ROOTS', str(material['wrong_root_path']))
    monkeypatch.setenv('SIGNATURE_INTERMEDIATE_CERTS', str(material['intermediate_path']))

    result = verify_signed_pdf(material['signed_path'])

    assert result['document_integrity_valid'] is True
    assert result['cryptographic_valid'] is True
    assert result['certificate_chain_valid'] is False
    assert result['reason_code'] == 'CERTIFICATE_TRUST_FAILURE'


def test_leaf_certificate_cannot_be_configured_as_trust_root(
    certificate_chain_signed_pdf_factory, monkeypatch
):
    import signature_verifier

    material = certificate_chain_signed_pdf_factory
    monkeypatch.setenv('SIGNATURE_TRUST_ROOTS', str(material['leaf_path']))
    with pytest.raises(signature_verifier.SignatureTrustConfigurationError) as error:
        signature_verifier.load_signature_trust_configuration()
    assert error.value.reason_code == 'INVALID_TRUST_CONFIGURATION'


def test_intermediate_certificate_cannot_be_configured_as_trust_root(
    certificate_chain_signed_pdf_factory, monkeypatch
):
    import signature_verifier

    material = certificate_chain_signed_pdf_factory
    monkeypatch.setenv('SIGNATURE_TRUST_ROOTS', str(material['intermediate_path']))
    with pytest.raises(signature_verifier.SignatureTrustConfigurationError) as error:
        signature_verifier.load_signature_trust_configuration()
    assert error.value.reason_code == 'INVALID_TRUST_CONFIGURATION'


def test_invalid_cms_signature_with_intact_pdf(signed_pdf_factory, tmp_path):
    from io import BytesIO
    from pypdf import PdfWriter
    from pyhanko.pdf_utils.reader import PdfFileReader
    from signature_verifier import signature_verification_result
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    output = BytesIO()
    writer.write(output)
    signed = signed_pdf_factory(output.getvalue())
    embedded = PdfFileReader(BytesIO(signed)).embedded_signatures[0]
    signature_hex = embedded.signer_info['signature'].native.hex().encode()
    offset = signed.lower().index(signature_hex)
    # Change only the CMS signature value, outside the signed PDF byte ranges.
    changed = signed[:offset] + (b'0' if signed[offset:offset + 1] != b'0' else b'1') + signed[offset + 1:]
    path = tmp_path / 'invalid-cms.pdf'
    path.write_bytes(changed)
    result = verify_signed_pdf(path)
    assert result['integrity_ok']
    assert not result['cryptographically_valid']
    assert result['reason_code'] == 'SIGNATURE_CRYPTOGRAPHIC_FAILURE'
    assert signature_verification_result(result, ['mszafir']) == 'INVALID_SIGNATURE'


@pytest.mark.parametrize('result', [{}, {'validation_status': 'VALID'}, {'is_signed': True, 'validation_status': 'VALID'}])
def test_incomplete_verifier_result_is_not_a_cryptographic_failure(result):
    from signature_verifier import signature_verification_result
    assert signature_verification_result(result, ['mszafir']) == 'VERIFICATION_ERROR'


def test_unsupported_pdf_signature_subfilter(signed_pdf_factory, tmp_path):
    from io import BytesIO
    from pypdf import PdfWriter
    from signature_verifier import signature_verification_result
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    output = BytesIO()
    writer.write(output)
    signed = signed_pdf_factory(output.getvalue())
    changed = signed.replace(b'/ETSI.CAdES.detached', b'/ETSI.XAdES.detached')
    assert changed != signed and len(changed) == len(signed)
    path = tmp_path / 'unsupported.pdf'
    path.write_bytes(changed)
    result = verify_signed_pdf(path)
    assert result['reason_code'] == 'UNSUPPORTED_SIGNATURE_FORMAT'
    assert signature_verification_result(result, ['mszafir']) == 'UNSUPPORTED_SIGNATURE'
    assert result['cryptographic_valid'] is None


def test_eurocert_detection_does_not_select_mszafir_or_profile(signed_pdf_factory, tmp_path):
    from io import BytesIO
    from pypdf import PdfWriter
    from signature_verifier import resolve_signature_policy, signature_verification_result
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    output = BytesIO()
    writer.write(output)
    path = tmp_path / 'other-provider.pdf'
    path.write_bytes(signed_pdf_factory(output.getvalue(), 'qualified_other'))
    result = verify_signed_pdf(path)
    assert result['cryptographic_valid'] is True
    assert result['provider'] == 'eurocert'
    assert result['detected_signature_type'] == 'qualified_other'
    policy = resolve_signature_policy(result, ['mszafir', 'profil_zaufany'])
    assert policy['provider_allowed'] is False
    assert policy['verifier_backend'] == 'pyhanko_pdf_cms'
    assert signature_verification_result(result, ['mszafir', 'profil_zaufany']) == 'SIGNATURE_TYPE_NOT_ALLOWED'
    assert signature_verification_result(result, ['qualified']) == 'VALID_SIGNATURE'


def test_policy_checks_each_signature():
    from signature_verifier import resolve_signature_policy
    result = {'signature_type': 'mszafir', 'signatures': [{'signature_type': 'mszafir'}, {'signature_type': 'profil_zaufany'}]}
    assert resolve_signature_policy(result, ['mszafir'])['provider_allowed'] is True
    assert resolve_signature_policy(result, ['mszafir', 'profil_zaufany'])['provider_allowed'] is True


def test_multiple_incremental_signatures_are_reported_and_a_valid_later_signature_wins(signed_pdf_factory, tmp_path):
    from io import BytesIO
    from pypdf import PdfWriter
    from signature_verifier import signature_verification_result

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    output = BytesIO()
    writer.write(output)
    first = signed_pdf_factory(output.getvalue(), 'profil_zaufany', 'Signature1')
    second = signed_pdf_factory(first, 'mszafir', 'Signature2')
    path = tmp_path / 'twice-signed.pdf'
    path.write_bytes(second)

    result = verify_signed_pdf(path)

    assert result['signature_count'] == 2
    assert len(result['signatures']) == 2
    assert all(item['coverage'] for item in result['signatures'])
    assert signature_verification_result(result, ['mszafir']) == 'VALID_SIGNATURE'


@pytest.mark.parametrize('key_kind, mechanism_hint', [
    ('rsa', 'rsa'),
    ('rsa_pss', 'pss'),
    ('ecdsa', 'ecdsa'),
])
def test_pyhanko_algorithm_dispatch_is_used(signed_pdf_factory, tmp_path, key_kind, mechanism_hint):
    from io import BytesIO
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    output = BytesIO()
    writer.write(output)
    path = tmp_path / f'{key_kind}.pdf'
    path.write_bytes(signed_pdf_factory(output.getvalue(), key_kind=key_kind))

    result = verify_signed_pdf(path)

    assert result['result'] == 'VALID_SIGNATURE', result
    mechanism = result['signatures'][0]['pkcs7_signature_mechanism'].lower()
    assert mechanism_hint in mechanism


def test_mapping_uses_granular_status_instead_of_bottom_line():
    from signature_verifier import signature_verification_result

    base = {
        'is_signed': True,
        'validation_status': 'UNTRUSTED',
        'signatures': [{
            'integrity_ok': True,
            'cryptographically_valid': True,
            'trusted': False,
            'revoked': False,
            'docmdp_ok': True,
            'timestamp_invalid': False,
            'verification_error': False,
            'signature_type': 'mszafir',
            'bottom_line': False,
        }],
        'reason_code': 'CERTIFICATE_TRUST_FAILURE',
    }
    assert signature_verification_result(base, ['mszafir']) == 'VERIFICATION_ERROR'
    base['signatures'][0].update(trusted=True, bottom_line=False)
    base['validation_status'] = 'VALID'
    assert signature_verification_result(base, ['mszafir']) == 'VALID_SIGNATURE'
    base['signatures'][0]['timestamp_status'] = 'INDETERMINATE'
    assert signature_verification_result(base, ['mszafir']) == 'VERIFICATION_ERROR'


def test_default_signature_result_is_unsigned():
    result = _build_default_result()

    assert result["is_signed"] is False
    assert result["is_valid_structure"] is False
    assert result["signature_type"] == "unknown"
    assert result["is_allowed_signature"] is False


def test_match_any_is_case_insensitive():
    assert _match_any("podpis wykonany przez profil zaufany", ["PROFIL ZAUFANY"]) is True


def test_classify_signature_detects_mszafir():
    result = _classify_signature("issuer: Krajowa Izba Rozliczeniowa COPE SZAFIR")

    assert result["provider"] == "szafir"
    assert result["signature_type"] == "mszafir"
    assert result["is_allowed_signature"] is True
    assert result["is_szafir_signature"] is True
    assert result["likely_mobywatel"] is True


def test_classify_signature_detects_trusted_profile():
    result = _classify_signature("Minister Cyfryzacji Profil Zaufany ePUAP")

    assert result["provider"] == "profil_zaufany"
    assert result["signature_type"] == "profil_zaufany"
    assert result["is_allowed_signature"] is True
    assert result["is_trusted_profile_signature"] is True
    assert result["likely_mobywatel"] is True


def test_classify_signature_prefers_trusted_profile_seal_role_over_eurocert_provider():
    from signature_verifier import resolve_signature_policy

    result = _classify_signature("EUROCERT | Minister do spraw informatyzacji - pieczęć podpisu zaufanego")

    assert result["provider"] == "eurocert"
    assert result["signature_type"] == "profil_zaufany"
    policy = resolve_signature_policy(result, ["profil_zaufany"])
    assert policy["provider_allowed"] is True


def test_classify_signature_rejects_unknown_provider():
    result = _classify_signature("Unknown Certificate Authority")

    assert result["provider"] == "other"
    assert result["signature_type"] == "unsupported"
    assert result["is_allowed_signature"] is False


def test_verify_signed_pdf_without_signature_field_returns_unsigned(monkeypatch, tmp_path):
    import signature_verifier

    pdf_path = tmp_path / "unsigned.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(signature_verifier, "_extract_pdf_signature", lambda path: None)

    result = verify_signed_pdf(pdf_path)

    assert result["is_signed"] is False
    assert result["is_allowed_signature"] is False
    assert result["reason"] == "Brak pola podpisu PDF."


def _replace_pdf_cms(pdf_bytes, mutate):
    """Replace CMS inside the excluded /Contents gap without changing PDF length."""
    from asn1crypto import cms
    from services.signatures.trusted_profile import _extract_pdf_signatures

    signature = _extract_pdf_signatures(pdf_bytes)[0]
    content_info = cms.ContentInfo.load(signature.contents)
    mutate(content_info["content"]["signer_infos"][0])
    encoded = content_info.dump()
    assert len(encoded) <= len(signature.contents)
    replacement = encoded + (b"\x00" * (len(signature.contents) - len(encoded)))
    gap_start = signature.byte_range[0] + signature.byte_range[1]
    gap_end = signature.byte_range[2]
    gap = pdf_bytes[gap_start:gap_end]
    for original_hex in (signature.contents.hex().encode(), signature.contents.hex().upper().encode()):
        offset = gap.find(original_hex)
        if offset >= 0:
            replacement_hex = replacement.hex().encode()
            if original_hex[:1].isupper():
                replacement_hex = replacement_hex.upper()
            changed_gap = gap[:offset] + replacement_hex + gap[offset + len(original_hex):]
            return pdf_bytes[:gap_start] + changed_gap + pdf_bytes[gap_end:]
    raise AssertionError("CMS hex payload not found inside /Contents gap")


def test_minimal_profile_rejects_signed_byte_range_modification(
    certificate_chain_signed_pdf_factory, monkeypatch, tmp_path
):
    material = certificate_chain_signed_pdf_factory
    monkeypatch.setenv("SIGNATURE_TRUST_ROOTS", str(material["root_path"]))
    monkeypatch.setenv("SIGNATURE_INTERMEDIATE_CERTS", str(material["intermediate_path"]))
    monkeypatch.setenv("TRUSTED_PROFILE_PYHANKO_DIAGNOSTIC", "false")
    original = material["signed_path"].read_bytes()
    changed = original.replace(
        b"/MediaBox [ 0.0 0.0 200 200 ]",
        b"/MediaBox [ 0.0 0.0 201 200 ]",
        1,
    )
    assert changed != original
    path = tmp_path / "changed.pdf"
    path.write_bytes(changed)

    result = verify_signed_pdf(path)

    assert result["document_integrity_valid"] is False
    assert result["result"] == "INVALID_SIGNATURE"
    assert result["reason_code"] == "DOCUMENT_INTEGRITY_FAILURE"


def test_minimal_profile_rejects_changed_signature_value(
    certificate_chain_signed_pdf_factory, monkeypatch, tmp_path
):
    material = certificate_chain_signed_pdf_factory
    monkeypatch.setenv("SIGNATURE_TRUST_ROOTS", str(material["root_path"]))
    monkeypatch.setenv("SIGNATURE_INTERMEDIATE_CERTS", str(material["intermediate_path"]))
    monkeypatch.setenv("TRUSTED_PROFILE_PYHANKO_DIAGNOSTIC", "false")
    changed = _replace_pdf_cms(
        material["signed_path"].read_bytes(),
        lambda signer_info: signer_info.__setitem__(
            "signature", signer_info["signature"].native[:-1] + bytes([signer_info["signature"].native[-1] ^ 1])
        ),
    )
    path = tmp_path / "changed-signature.pdf"
    path.write_bytes(changed)

    result = verify_signed_pdf(path)

    assert result["document_integrity_valid"] is True
    assert result["cryptographic_valid"] is False
    assert result["result"] == "INVALID_SIGNATURE"
    assert result["reason_code"] == "SIGNATURE_CRYPTOGRAPHIC_FAILURE"


def test_minimal_profile_rejects_malformed_pkcs1_padding():
    from hashlib import sha256
    from services.signatures.trusted_profile import (
        _SHA256_DIGEST_INFO_WITH_NULL,
        _validate_pkcs1_v15_block,
    )

    digest = sha256(b"signed attrs").digest()
    valid = b"\x00\x01" + (b"\xff" * 8) + b"\x00" + _SHA256_DIGEST_INFO_WITH_NULL + digest
    assert _validate_pkcs1_v15_block(valid, digest) is True
    assert _validate_pkcs1_v15_block(b"\x00\x01" + (b"\xff" * 7) + valid[10:], digest) is False
    assert _validate_pkcs1_v15_block(valid[:5] + b"\xfe" + valid[6:], digest) is False
    assert _validate_pkcs1_v15_block(valid + b"extra", digest) is False


def test_minimal_profile_matches_signer_certificate_by_sid(
    certificate_chain_signed_pdf_factory
):
    from services.signatures.trusted_profile import (
        TrustedProfileVerificationError,
        _extract_pdf_signatures,
        _find_signer_certificate,
        _load_signed_data,
    )

    signature = _extract_pdf_signatures(
        certificate_chain_signed_pdf_factory["signed_path"].read_bytes()
    )[0]
    signed_data = _load_signed_data(signature.contents)
    signer_info = signed_data["signer_infos"][0]
    assert _find_signer_certificate(signed_data, signer_info).serial_number == signer_info["sid"].chosen["serial_number"].native
    signer_info["sid"].chosen["serial_number"] = signer_info["sid"].chosen["serial_number"].native + 1
    with pytest.raises(TrustedProfileVerificationError) as error:
        _find_signer_certificate(signed_data, signer_info)
    assert error.value.reason_code == "SIGNER_CERTIFICATE_NOT_FOUND"


def test_minimal_profile_rejects_wrong_intermediate(
    certificate_chain_signed_pdf_factory, monkeypatch
):
    material = certificate_chain_signed_pdf_factory
    monkeypatch.setenv("SIGNATURE_TRUST_ROOTS", str(material["root_path"]))
    monkeypatch.setenv("SIGNATURE_INTERMEDIATE_CERTS", str(material["wrong_root_path"]))
    monkeypatch.setenv("TRUSTED_PROFILE_PYHANKO_DIAGNOSTIC", "false")

    result = verify_signed_pdf(material["signed_path"])

    assert result["cryptographic_valid"] is True
    assert result["certificate_chain_valid"] is False
    assert result["reason_code"] == "CERTIFICATE_TRUST_FAILURE"


def test_minimal_profile_rejects_expired_chain_at_validation_time(
    certificate_chain_signed_pdf_factory
):
    from datetime import datetime, timezone
    from cryptography import x509
    from services.signatures.trusted_profile import (
        TrustedProfileVerificationError,
        _extract_pdf_signatures,
        _find_signer_certificate,
        _load_signed_data,
        _verify_local_chain,
    )

    material = certificate_chain_signed_pdf_factory
    signature = _extract_pdf_signatures(material["signed_path"].read_bytes())[0]
    signed_data = _load_signed_data(signature.contents)
    signer = x509.load_der_x509_certificate(
        _find_signer_certificate(signed_data, signed_data["signer_infos"][0]).dump()
    )
    future = datetime(2200, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(TrustedProfileVerificationError) as error:
        _verify_local_chain(
            signer,
            [material["intermediate_path"]],
            [material["root_path"]],
            moment=future,
        )
    assert error.value.reason_code == "CERTIFICATE_EXPIRED"


def test_minimal_profile_applies_allowed_signatures_after_technical_validation(
    certificate_chain_signed_pdf_factory, monkeypatch
):
    from signature_verifier import signature_verification_result

    material = certificate_chain_signed_pdf_factory
    monkeypatch.setenv("SIGNATURE_TRUST_ROOTS", str(material["root_path"]))
    monkeypatch.setenv("SIGNATURE_INTERMEDIATE_CERTS", str(material["intermediate_path"]))
    monkeypatch.setenv("TRUSTED_PROFILE_PYHANKO_DIAGNOSTIC", "false")
    result = verify_signed_pdf(material["signed_path"])

    assert signature_verification_result(result, ["profil_zaufany"]) == "VALID_SIGNATURE"
    assert signature_verification_result(result, ["mszafir"]) == "SIGNATURE_TYPE_NOT_ALLOWED"


def test_trusted_profile_router_falls_back_to_cms_signer_when_pyhanko_detection_is_empty(
    certificate_chain_signed_pdf_factory, monkeypatch
):
    import signature_verifier

    material = certificate_chain_signed_pdf_factory
    monkeypatch.setenv("SIGNATURE_TRUST_ROOTS", str(material["root_path"]))
    monkeypatch.setenv("SIGNATURE_INTERMEDIATE_CERTS", str(material["intermediate_path"]))
    monkeypatch.setenv("TRUSTED_PROFILE_PYHANKO_DIAGNOSTIC", "false")

    class EmptyPyHankoReader:
        def __init__(self, _stream):
            self.embedded_signatures = []

    monkeypatch.setattr(signature_verifier, "PdfFileReader", EmptyPyHankoReader)
    result = verify_signed_pdf(material["signed_path"])

    assert result["verifier_backend"] == "trusted_profile_minimal_cms"
    assert result["detected_signature_type"] == "profil_zaufany"
    assert result["signature_count"] == 1
    assert result["result"] == "VALID_SIGNATURE"


def test_mszafir_stays_on_pyhanko_backend(signed_pdf_factory, monkeypatch, tmp_path):
    from io import BytesIO
    from pypdf import PdfWriter

    monkeypatch.setenv("TRUSTED_PROFILE_PYHANKO_DIAGNOSTIC", "false")
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    output = BytesIO()
    writer.write(output)
    path = tmp_path / "mszafir.pdf"
    path.write_bytes(signed_pdf_factory(output.getvalue(), "mszafir", "Signature1"))

    result = verify_signed_pdf(path)

    assert result["verifier_backend"] == "pyhanko_pdf_cms"
    assert result["result"] == "VALID_SIGNATURE"
