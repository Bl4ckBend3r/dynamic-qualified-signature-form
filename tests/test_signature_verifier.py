from signature_verifier import _build_default_result, _classify_signature, _match_any, verify_signed_pdf


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


def test_classify_signature_rejects_unknown_provider():
    result = _classify_signature("Unknown Certificate Authority")

    assert result["provider"] == "other"
    assert result["signature_type"] == "unsupported"
    assert result["is_allowed_signature"] is False


def test_verify_signed_pdf_without_signature_field_returns_unsigned(tmp_path):
    pdf_path = tmp_path / "unsigned.pdf"
    pdf_path.write_bytes(
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Count 0 >> endobj\n"
        b"trailer << /Root 1 0 R >>\n"
        b"%%EOF"
    )

    result = verify_signed_pdf(pdf_path)

    assert result["is_signed"] is False
    assert result["is_allowed_signature"] is False
    assert result["reason"] == "Brak pola podpisu PDF."
