from decimal import Decimal

from services.training_service import (
    format_price_pln,
    normalize_training_dates,
    normalize_trainings_config,
    parse_training_snapshots,
)


def test_format_price_pln_handles_decimal_int_string_and_none():
    assert format_price_pln(Decimal("1200")) == "1 200,00 zł"
    assert format_price_pln(0) == "0,00 zł"
    assert format_price_pln("1234,5") == "1 234,50 zł"
    assert format_price_pln(None) == "Brak danych o cenie"


def test_parse_training_snapshots_normalizes_legacy_training_price():
    snapshots = parse_training_snapshots(
        '[{"training_id":"excel","training_name":"Excel","training_price":"900.5"}]'
    )

    assert snapshots[0]["id"] == "excel"
    assert snapshots[0]["name"] == "Excel"
    assert snapshots[0]["price"] == "900.50"
    assert snapshots[0]["price_formatted"] == "900,50 zł"
    assert snapshots[0]["dates"] == []


def test_normalize_training_dates_sorts_and_formats_labels():
    dates = normalize_training_dates(
        [
            {"start_date": "2026-09-10", "start_time": "09:00", "end_time": "12:00"},
            {"start_date": "2026-08-01", "location": "Sala 1"},
        ]
    )

    assert [item["start_date"] for item in dates] == ["2026-08-01", "2026-09-10"]
    assert dates[1]["label"] == "10.09.2026, 09:00–12:00"


def test_normalize_trainings_config_converts_legacy_date_text():
    catalog = normalize_trainings_config(
        {
            "catalog": [
                {
                    "id": "excel",
                    "name": "Excel",
                    "dates": "2026-08-12|2026-08-12|09:00|15:00|Zielona Góra|Warsztat",
                }
            ]
        }
    )

    assert catalog[0]["dates"][0]["label"] == "12.08.2026, 09:00–15:00"
    assert catalog[0]["dates"][0]["location"] == "Zielona Góra"
    assert "code" not in catalog[0]
