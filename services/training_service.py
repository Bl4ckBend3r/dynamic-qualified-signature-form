from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping


PRICE_MISSING_LABEL = "Brak danych o cenie"
DEFAULT_CURRENCY = "PLN"
LOW_SEATS_THRESHOLD = 5


def parse_decimal_price(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = str(value).strip().replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        return Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


def decimal_price_to_storage(value: Any) -> str | None:
    price = parse_decimal_price(value)
    return f"{price:.2f}" if price is not None else None


def format_price_pln(value: Any, currency: str | None = None) -> str:
    price = parse_decimal_price(value)
    if price is None:
        return PRICE_MISSING_LABEL
    currency_label = (currency or DEFAULT_CURRENCY).strip().upper()
    amount = f"{price:,.2f}".replace(",", " ").replace(".", ",")
    suffix = "zł" if currency_label == "PLN" else currency_label
    return f"{amount} {suffix}"


def normalize_training_id(value: Any) -> str:
    return str(value or "").strip()


def is_training_active(item: Mapping[str, Any]) -> bool:
    value = item.get("active", True)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "nie", "no", "off", "inactive"}


def normalize_training_catalog(field: Mapping[str, Any] | None, *, active_only: bool = True) -> list[dict]:
    currency = str((field or {}).get("currency") or DEFAULT_CURRENCY).strip() or DEFAULT_CURRENCY
    items = []
    for index, item in enumerate((field or {}).get("catalog") or []):
        if not isinstance(item, Mapping):
            continue
        if active_only and not is_training_active(item):
            continue
        name = str(item.get("name") or item.get("label") or item.get("id") or "").strip()
        if not name:
            continue
        training_id = normalize_training_id(item.get("id") or name)
        storage_price = decimal_price_to_storage(item.get("price"))
        capacity = parse_capacity(item.get("capacity", item.get("seat_limit")))
        items.append(
            {
                "id": training_id,
                "name": name,
                "price": storage_price,
                "price_formatted": format_price_pln(storage_price, item.get("currency") or currency),
                "currency": str(item.get("currency") or currency).strip() or DEFAULT_CURRENCY,
                "description": str(item.get("description") or "").strip(),
                "location": str(item.get("location") or "").strip(),
                "active": is_training_active(item),
                "sort_order": int(item.get("sort_order") or index + 1),
                "capacity": capacity,
                "dates": normalize_training_dates(
                    item.get("dates", item.get("training_dates", item.get("dates_text")))
                ),
                "low_seats_comment": str(item.get("low_seats_comment") or "").strip(),
            }
        )
    return sorted(items, key=lambda item: (item["sort_order"], item["name"].lower()))


def normalize_trainings_config(field: Mapping[str, Any] | None, *, active_only: bool = True) -> list[dict]:
    """Return the canonical catalog shared by admin, declarations and documents."""
    return normalize_training_catalog(field, active_only=active_only)


def selected_training_snapshots(
    field: Mapping[str, Any],
    request_form,
    availability: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[list[dict], str | None]:
    selected_ids = {normalize_training_id(value) for value in request_form.getlist(field.get("name", ""))}
    catalog = normalize_training_catalog(field, active_only=True)
    selected = []
    availability = availability or {}
    for item in catalog:
        if item["id"] not in selected_ids:
            continue
        available_item = {**dict(item), **dict(availability.get(item["id"], {}))}
        if available_item.get("available_seats") == 0:
            return [], f"Brak wolnych miejsc dla szkolenia: {item['name']}."
        selected.append(available_item)

    if field.get("required") and not selected:
        return [], "Wybierz co najmniej jedno szkolenie."

    max_total = parse_decimal_price(field.get("max_total_amount"))
    total = sum((parse_decimal_price(item.get("price")) or Decimal("0.00")) for item in selected)
    if max_total is not None and total > max_total:
        return selected, (
            f"Łączna wartość szkoleń przekracza limit "
            f"{format_price_pln(max_total, field.get('currency') or DEFAULT_CURRENCY)}."
        )

    return selected, None


def parse_training_snapshots(value: Any) -> list[dict]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return parse_training_snapshots(decoded)
    if isinstance(value, Mapping):
        return [normalize_training_snapshot(value)]
    if isinstance(value, list):
        return [
            item
            for item in (
                normalize_training_snapshot(entry)
                for entry in value
                if isinstance(entry, Mapping)
            )
            if item
        ]
    return []


def normalize_training_snapshot(item: Mapping[str, Any]) -> dict:
    name = str(item.get("name") or item.get("training_name") or item.get("label") or item.get("id") or "").strip()
    if not name:
        return {}
    price = item.get("price", item.get("training_price"))
    currency = str(item.get("currency") or DEFAULT_CURRENCY).strip() or DEFAULT_CURRENCY
    return {
        "id": str(item.get("id") or item.get("training_id") or item.get("value") or name).strip(),
        "name": name,
        "price": decimal_price_to_storage(price),
        "price_formatted": format_price_pln(price, currency),
        "currency": currency,
        "description": str(item.get("description") or "").strip(),
        "dates": normalize_training_dates(item.get("dates")),
    }


def parse_capacity(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        capacity = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return max(capacity, 0)


def normalize_training_dates(value: Any) -> list[dict]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            decoded = parse_training_dates_lines(text)
        return normalize_training_dates(decoded)
    if isinstance(value, Mapping):
        normalized = normalize_training_date(value)
        return [normalized] if normalized else []
    if isinstance(value, list):
        dates = [item for item in (normalize_training_date(entry) for entry in value if isinstance(entry, Mapping)) if item]
        return sorted(dates, key=lambda item: (item["start_date"], item.get("start_time") or ""))
    return []


def normalize_training_date(item: Mapping[str, Any]) -> dict:
    start_date = str(item.get("start_date") or item.get("date") or "").strip()
    if not is_iso_date(start_date):
        return {}
    end_date = str(item.get("end_date") or "").strip()
    if end_date and not is_iso_date(end_date):
        end_date = ""
    start_time = normalize_time(item.get("start_time"))
    end_time = normalize_time(item.get("end_time"))
    return {
        "start_date": start_date,
        "end_date": end_date,
        "start_time": start_time,
        "end_time": end_time,
        "location": str(item.get("location") or "").strip(),
        "description": str(item.get("description") or "").strip(),
        "label": format_training_date_label(start_date, end_date, start_time, end_time),
    }


def parse_training_dates_lines(value: str) -> list[dict]:
    dates = []
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split("|")]
        dates.append(
            {
                "start_date": parts[0] if len(parts) > 0 else "",
                "end_date": parts[1] if len(parts) > 1 else "",
                "start_time": parts[2] if len(parts) > 2 else "",
                "end_time": parts[3] if len(parts) > 3 else "",
                "location": parts[4] if len(parts) > 4 else "",
                "description": parts[5] if len(parts) > 5 else "",
            }
        )
    return dates


def is_iso_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def normalize_time(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        datetime.strptime(text, "%H:%M")
    except ValueError:
        return ""
    return text


def format_training_date_label(start_date: str, end_date: str = "", start_time: str = "", end_time: str = "") -> str:
    start = datetime.strptime(start_date, "%Y-%m-%d").strftime("%d.%m.%Y")
    if end_date and end_date != start_date:
        label = f"{start}–{datetime.strptime(end_date, '%Y-%m-%d').strftime('%d.%m.%Y')}"
    else:
        label = start
    if start_time and end_time:
        return f"{label}, {start_time}–{end_time}"
    if start_time:
        return f"{label}, {start_time}"
    return label


def build_training_availability(catalog: list[dict], occupied_counts: Mapping[str, int]) -> dict[str, dict]:
    result = {}
    for item in catalog:
        capacity = parse_capacity(item.get("capacity"))
        occupied = max(int(occupied_counts.get(item["id"], 0) or 0), 0)
        available = None if capacity is None else max(capacity - occupied, 0)
        low_comment = item.get("low_seats_comment") if available is not None and 0 < available <= LOW_SEATS_THRESHOLD else ""
        result[item["id"]] = {
            **item,
            "occupied_seats": occupied,
            "available_seats": available,
            "available_seats_label": availability_label(available),
            "is_available": available is None or available > 0,
            "low_seats_comment_visible": low_comment,
        }
    return result


def availability_label(available: int | None) -> str:
    if available is None:
        return "Dostępne miejsca: Brak danych"
    if available <= 0:
        return "Brak wolnych miejsc"
    return f"Dostępne miejsca: {available}"


def format_admin_value(value: Any) -> str:
    if value in (None, ""):
        return "Brak danych"
    if isinstance(value, bool):
        return "Tak" if value else "Nie"
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return "Tak"
        if lowered == "false":
            return "Nie"
    return str(value)
