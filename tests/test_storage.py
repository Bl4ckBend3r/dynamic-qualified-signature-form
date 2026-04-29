import csv
from pathlib import Path


def test_submit_valid_form_creates_csv_file(app, client, valid_form_data):
    response = client.post(
        "/submit/sample_form",
        data=valid_form_data,
        follow_redirects=True,
    )

    assert response.status_code == 200

    csv_path = Path(app.config["CSV_OUTPUT_PATH"])

    assert csv_path.exists()
    assert csv_path.stat().st_size > 0


def test_csv_contains_submitted_candidate_data(app, client, valid_form_data):
    client.post(
        "/submit/sample_form",
        data=valid_form_data,
        follow_redirects=True,
    )

    csv_path = Path(app.config["CSV_OUTPUT_PATH"])

    with csv_path.open("r", encoding="utf-8", newline="") as file:
        content = file.read()

    assert "Jan" in content
    assert "Kowalski" in content
    assert "jan.kowalski@example.com" in content