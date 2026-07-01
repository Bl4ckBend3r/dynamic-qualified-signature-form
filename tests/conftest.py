import csv
import io
import os
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def form_definition():
    from form_loader import normalize_form_definition

    return normalize_form_definition(
        {
            "title": "Formularz zgłoszeniowy",
            "description": "Formularz testowy projektu Wiedza kluczem do sukcesu.",
            "fields": [
                {"type": "section", "label": "Dane kandydata"},
                {"type": "text", "name": "imie", "label": "Imię", "required": True},
                {"type": "text", "name": "nazwisko", "label": "Nazwisko", "required": True},
                {"type": "text", "name": "obywatelstwo", "label": "Obywatelstwo", "required": True},
                {"type": "date", "name": "data_urodzenia", "label": "Data urodzenia", "required": True},
                {"type": "text", "name": "miejsce_urodzenia", "label": "Miejsce urodzenia", "required": True},
                {"type": "pesel", "name": "pesel", "label": "PESEL", "required": True},
                {"type": "radio", "name": "plec", "label": "Płeć", "required": True, "options": ["Kobieta", "Mężczyzna", "inna"]},
                {"type": "number", "name": "wiek", "label": "Wiek", "required": True},
                {"type": "select", "name": "wyksztalcenie", "label": "Wykształcenie", "required": True, "options": ["Podstawowe", "Ponadgimnazjalne", "Wyższe magisterskie"]},
                {"type": "section", "label": "Adres zamieszkania"},
                {"type": "text", "name": "wojewodztwo", "label": "Województwo", "required": True},
                {"type": "text", "name": "powiat", "label": "Powiat", "required": True},
                {"type": "text", "name": "gmina", "label": "Gmina", "required": True},
                {"type": "text", "name": "miejscowosc", "label": "Miejscowość", "required": True},
                {"type": "text", "name": "kod_pocztowy", "label": "Kod pocztowy", "required": True},
                {"type": "text", "name": "ulica", "label": "Ulica", "required": True},
                {"type": "text", "name": "nr_budynku", "label": "Nr budynku", "required": True},
                {"type": "text", "name": "nr_lokalu", "label": "Nr lokalu"},
                {"type": "section", "label": "Dane kontaktowe"},
                {"type": "tel", "name": "telefon", "label": "Telefon kontaktowy", "required": True},
                {"type": "email", "name": "email", "label": "Adres e-mail", "required": True},
                {"type": "section", "label": "Grupa docelowa"},
                {"type": "radio", "name": "zamieszkanie_lubuskie", "label": "Zamieszkanie na terenie województwa lubuskiego", "required": True, "options": ["Tak", "Nie"]},
                {"type": "radio", "name": "praca_lubuskie", "label": "Praca na terenie województwa lubuskiego", "required": True, "options": ["Tak", "Nie"]},
                {"type": "radio", "name": "osoba_z_niepelnosprawnosciami", "label": "Osoba z niepełnosprawnościami", "required": True, "options": ["Tak", "Nie", "Odmowa podania informacji"]},
                {"type": "textarea", "name": "specjalne_potrzeby", "label": "Specjalne potrzeby"},
                {"type": "section", "label": "Oświadczenia"},
                {"type": "checkbox", "name": "accept_regulamin", "label": "Akceptacja regulaminu", "required": True, "options": [{"value": "Tak", "label": "Akceptuję regulamin rekrutacji i uczestnictwa."}]},
                {"type": "checkbox", "name": "accept_rodo", "label": "Zgoda RODO", "required": True, "options": [{"value": "Tak", "label": "Wyrażam zgodę na przetwarzanie danych osobowych."}]},
                {"type": "checkbox", "name": "accept_odpowiedzialnosc", "label": "Odpowiedzialność cywilna", "required": True, "options": [{"value": "Tak", "label": "Oświadczam, że dane są zgodne z prawdą."}]},
            ],
            "documents": {
                "declaration": {"enabled": False},
                "agreement": {"enabled": False},
            },
        }
    )


@pytest.fixture()
def valid_form_data():
    return {
        "imie": "Jan",
        "nazwisko": "Kowalski",
        "obywatelstwo": "polskie",
        "data_urodzenia": "1990-01-01",
        "miejsce_urodzenia": "Zielona Góra",
        "pesel": "90010112356",
        "plec": "Mężczyzna",
        "wiek": "36",
        "wyksztalcenie": "Wyższe magisterskie",
        "wojewodztwo": "lubuskie",
        "powiat": "zielonogórski",
        "gmina": "Zielona Góra",
        "miejscowosc": "Zielona Góra",
        "kod_pocztowy": "65-001",
        "ulica": "Testowa",
        "nr_budynku": "1",
        "nr_lokalu": "2",
        "telefon": "600700800",
        "email": "jan.kowalski@example.com",
        "zamieszkanie_lubuskie": "Tak",
        "praca_lubuskie": "Tak",
        "osoba_z_niepelnosprawnosciami": "Nie",
        "specjalne_potrzeby": "nie dotyczy",
        "accept_regulamin": "Tak",
        "accept_rodo": "Tak",
        "accept_odpowiedzialnosc": "Tak",
    }


class InMemoryStorage:
    def __init__(self, form_definition):
        self.form_definition = form_definition
        self.form_filename = "formularz_zgloszeniowy.json"
        self.output_dir = "output"
        self.csv_filename = "dane.csv"
        self.saved_pdfs = {}
        self.csv_rows = []
        self.direct_files = {}

    def ensure_base_structure(self):
        return None

    def ensure_outputs_for_all_forms(self):
        return None

    def ensure_form_output_structure(self, slug):
        return None

    def list_form_files(self):
        return [self.form_filename]

    def read_form_json(self, filename):
        if filename != self.form_filename:
            raise FileNotFoundError(filename)
        return self.form_definition

    def exists(self, path):
        if path.endswith(f"/{self.csv_filename}") and self.csv_rows:
            return True
        return path in self.direct_files or path in self.saved_pdfs

    def save_pdf(self, slug, filename, pdf_bytes, **kwargs):
        self.saved_pdfs[f"output/{slug}/pdf/{filename}"] = pdf_bytes

    def get_pdf_bytes(self, slug, filename):
        return self.saved_pdfs[f"output/{slug}/pdf/{filename}"]

    def get_file_bytes(self, path):
        if path.endswith(f"/{self.csv_filename}"):
            return self.read_text_or_empty(path).encode("utf-8")
        if path in self.direct_files:
            return self.direct_files[path]
        return self.saved_pdfs[path]

    def read_text_or_empty(self, path):
        if path.endswith(self.csv_filename):
            if not self.csv_rows:
                return ""
            fieldnames = []
            for row in self.csv_rows:
                for key in row.keys():
                    if key not in fieldnames:
                        fieldnames.append(key)
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.csv_rows)
            return buffer.getvalue()
        return self.direct_files.get(path, b"").decode("utf-8") if path in self.direct_files else ""

    def append_csv_row(self, slug, row):
        self.csv_rows.append(dict(row))

    def read_csv_rows(self, slug):
        return list(self.csv_rows)

    def update_csv_row_by_submission_id(self, slug, submission_id, updates):
        for row in self.csv_rows:
            if row.get("submission_id") == submission_id:
                row.update(updates)
                return True
        return False


@pytest.fixture()
def app(monkeypatch, tmp_path, form_definition):
    monkeypatch.setenv("FLASK_ENV", "testing")
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("NEXTCLOUD_BASE_URL", "https://nextcloud.test")
    monkeypatch.setenv("NEXTCLOUD_USERNAME", "tester")
    monkeypatch.setenv("NEXTCLOUD_APP_PASSWORD", "secret")
    monkeypatch.setenv("NEXTCLOUD_FORMS_DIR", "Formularze")
    monkeypatch.setenv("NEXTCLOUD_OUTPUT_DIR", "output")
    monkeypatch.setenv("TEMP_DIR", str(tmp_path / "tmp"))
    monkeypatch.setenv("DATABASE_URL", "")

    import app as app_module
    import legacy_app

    storage = InMemoryStorage(form_definition)

    def fake_generate_pdf(app, template_name, context, output_path):
        Path(output_path).write_bytes(b"%PDF-1.4\n% test pdf\n")

    monkeypatch.setattr(legacy_app, "generate_pdf", fake_generate_pdf)

    flask_app = app_module.create_app(storage_override=storage)
    flask_app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        SERVER_NAME="localhost",
        TEMP_DIR=tmp_path / "tmp",
        NEXTCLOUD_OUTPUT_DIR="output",
        NEXTCLOUD_FORMS_DIR="Formularze",
        CSV_FILENAME="dane.csv",
    )
    Path(flask_app.config["TEMP_DIR"]).mkdir(parents=True, exist_ok=True)
    flask_app.testing_storage = storage
    legacy_app.app = flask_app
    legacy_app.storage = storage
    legacy_app.submission_repository = flask_app.extensions["services"].submission_repository

    yield flask_app

    if hasattr(flask_app, "testing_storage"):
        delattr(flask_app, "testing_storage")


@pytest.fixture()
def client(app):
    return app.test_client()
