from pathlib import Path


def test_submit_valid_form_creates_pdf_file(app, client, valid_form_data):
    response = client.post(
        "/submit/sample_form",
        data=valid_form_data,
        follow_redirects=True,
    )

    assert response.status_code == 200

    generated_dir = Path(app.config["GENERATED_PDF_DIR"])
    pdf_files = list(generated_dir.glob("*.pdf"))

    assert len(pdf_files) >= 1
    assert pdf_files[0].stat().st_size > 0


def test_generated_pdf_has_pdf_header(app, client, valid_form_data):
    client.post(
        "/submit/sample_form",
        data=valid_form_data,
        follow_redirects=True,
    )

    generated_dir = Path(app.config["GENERATED_PDF_DIR"])
    pdf_files = list(generated_dir.glob("*.pdf"))

    assert pdf_files

    pdf_content = pdf_files[0].read_bytes()

    assert pdf_content.startswith(b"%PDF")