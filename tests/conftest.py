import os
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def app(monkeypatch, tmp_path):
    """
    Konfiguracja aplikacji Flask dla testów.

    Założenie:
    - aplikacja Flask znajduje się w pliku app.py jako zmienna `app`.

    Jeżeli masz factory pattern, np. create_app(), zamień import:
        from app import create_app
        flask_app = create_app()
    """

    monkeypatch.setenv("FLASK_ENV", "testing")
    monkeypatch.setenv("TESTING", "1")

    from app import app as flask_app

    flask_app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        SERVER_NAME="localhost",
    )

    test_output_dir = tmp_path / "output"
    test_output_dir.mkdir(parents=True, exist_ok=True)

    test_generated_dir = tmp_path / "generated"
    test_generated_dir.mkdir(parents=True, exist_ok=True)

    test_signed_dir = tmp_path / "signed"
    test_signed_dir.mkdir(parents=True, exist_ok=True)

    # Najczęściej używane konfiguracje w tej aplikacji.
    # Jeżeli nazwy configów są inne, dopasuj je do projektu.
    flask_app.config["OUTPUT_DIR"] = str(test_output_dir)
    flask_app.config["GENERATED_PDF_DIR"] = str(test_generated_dir)
    flask_app.config["SIGNED_PDF_DIR"] = str(test_signed_dir)
    flask_app.config["CSV_OUTPUT_PATH"] = str(test_output_dir / "submissions.csv")

    yield flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def valid_form_data():
    """
    Dane testowe odpowiadające formularzowi zgłoszeniowemu.

    Formularz źródłowy zawiera m.in. dane kandydata, adres,
    dane kontaktowe, przynależność do grupy docelowej oraz oświadczenia.
    :contentReference[oaicite:2]{index=2}
    """

    return {
        "imie": "Jan",
        "nazwisko": "Kowalski",
        "obywatelstwo": "polskie",
        "data_urodzenia": "1990-01-01",
        "miejsce_urodzenia": "Zielona Góra",
        "pesel": "90010112345",
        "plec": "mezczyzna",
        "wiek": "36",
        "wyksztalcenie": "wyzsze_magisterskie",

        "wojewodztwo": "lubuskie",
        "powiat": "zielonogorski",
        "gmina": "Zielona Góra",
        "miejscowosc": "Zielona Góra",
        "kod_pocztowy": "65-001",
        "ulica": "Testowa",
        "nr_budynku": "1",
        "nr_lokalu": "2",

        "telefon": "600700800",
        "email": "jan.kowalski@example.com",

        "zamieszkanie_lubuskie": "tak",
        "praca_lubuskie": "tak",

        "osoba_z_niepelnosprawnosciami": "nie",
        "specjalne_potrzeby": "nie",

        "miejscowosc_data": "Zielona Góra, 2026-04-29",

        "accept_regulamin": "on",
        "accept_kryteria": "on",
        "accept_ue": "on",
        "accept_formularz_nie_gwarantuje": "on",
        "accept_rodo": "on",
        "accept_ewaluacja": "on",
        "accept_zaswiadczenie": "on",
        "accept_monitoring": "on",
        "accept_odpowiedzialnosc": "on",
    }