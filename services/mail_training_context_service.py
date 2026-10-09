from __future__ import annotations

from decimal import Decimal
from html import escape
from typing import Any, Mapping

from services.training_catalog_service import TrainingCatalogService
from services.training_service import (
    build_training_availability,
    format_price_pln,
    parse_decimal_price,
    parse_training_snapshots,
)


NO_AVAILABLE_TRAININGS = "Brak skonfigurowanych szkoleń dla tego formularza."
NO_SELECTED_TRAININGS = "Brak wybranych szkoleń."


def find_training_field(form) -> dict[str, Any] | None:
    definition = getattr(form, "definition_json", None) or {}
    return _find_training_field(definition)


def build_training_mail_context(
    form,
    submission=None,
    *,
    availability_service=None,
    use_available_as_selected_fallback: bool = False,
) -> dict[str, Any]:
    field = find_training_field(form)
    available_items = _available_trainings(form, field, availability_service)
    selected_items = parse_training_snapshots(getattr(submission, "selected_trainings", "") if submission else "")
    available_by_id = {str(item.get("id") or ""): item for item in available_items}
    selected_items = [
        {**dict(available_by_id.get(str(item.get("id") or ""), {})), **item}
        for item in selected_items
    ]
    if not selected_items and use_available_as_selected_fallback and available_items:
        selected_items = [dict(available_items[0])]

    available_table = render_trainings_table(available_items, empty_message=NO_AVAILABLE_TRAININGS)
    available_list = render_trainings_list(available_items, empty_message=NO_AVAILABLE_TRAININGS)
    available_text = render_trainings_text(
        available_items,
        empty_message=NO_AVAILABLE_TRAININGS,
        heading="Dostępne szkolenia:",
    )
    selected_table = render_trainings_table(selected_items, empty_message=NO_SELECTED_TRAININGS)
    selected_list = render_trainings_list(selected_items, empty_message=NO_SELECTED_TRAININGS)
    selected_text = render_trainings_text(
        selected_items,
        empty_message=NO_SELECTED_TRAININGS,
        heading="Wybrane szkolenia:",
    )
    selected_total = sum(
        (parse_decimal_price(item.get("price")) or Decimal("0.00") for item in selected_items),
        Decimal("0.00"),
    )

    return {
        "available_trainings": available_table,
        "available_trainings_table": available_table,
        "available_trainings_list": available_list,
        "available_trainings_text": available_text,
        "available_trainings_list_text": available_text,
        "selected_trainings_table": selected_table,
        "selected_trainings_list": selected_list,
        "selected_trainings_text": selected_text,
        "selected_trainings_count": len(selected_items),
        "trainings_total_price": format_price_pln(selected_total),
    }


def render_trainings_table(items: list[Mapping[str, Any]], *, empty_message: str) -> str:
    if not items:
        return f'<p style="margin:0;">{escape(empty_message)}</p>'
    rows = []
    for item in items:
        details = _training_details_html(item)
        places = "<br>".join(
            (
                f"Limit: {_display(item.get('capacity'))}",
                f"Zajęte: {_display(item.get('occupied_seats', 0))}",
                f"Dostępne: {_display(item.get('available_seats'))}",
                f"Status: {escape(_availability_status(item))}",
            )
        )
        rows.append(
            "<tr>"
            f'<td style="padding:8px;border:1px solid #d9dde5;vertical-align:top;"><strong>{escape(str(item.get("name") or ""))}</strong>{details}</td>'
            f'<td style="padding:8px;border:1px solid #d9dde5;vertical-align:top;">{escape(str(item.get("price_formatted") or format_price_pln(item.get("price"), item.get("currency"))))}</td>'
            f'<td style="padding:8px;border:1px solid #d9dde5;vertical-align:top;">{places}</td>'
            f'<td style="padding:8px;border:1px solid #d9dde5;vertical-align:top;">{_dates_html(item)}</td>'
            "</tr>"
        )
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" width="100%" '
        'style="width:100%;border-collapse:collapse;">'
        "<thead><tr>"
        '<th style="padding:8px;border:1px solid #d9dde5;text-align:left;">Szkolenie</th>'
        '<th style="padding:8px;border:1px solid #d9dde5;text-align:left;">Cena</th>'
        '<th style="padding:8px;border:1px solid #d9dde5;text-align:left;">Miejsca</th>'
        '<th style="padding:8px;border:1px solid #d9dde5;text-align:left;">Terminy i lokalizacja</th>'
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def render_trainings_list(items: list[Mapping[str, Any]], *, empty_message: str) -> str:
    if not items:
        return f'<p style="margin:0;">{escape(empty_message)}</p>'
    entries = []
    for item in items:
        lines = [
            f'<strong>{escape(str(item.get("name") or ""))}</strong>',
            f'Cena: {escape(str(item.get("price_formatted") or format_price_pln(item.get("price"), item.get("currency"))))}',
            f'Limit miejsc: {_display(item.get("capacity"))}',
            f'Zajęte miejsca: {_display(item.get("occupied_seats", 0))}',
            f'Dostępne miejsca: {_display(item.get("available_seats"))}',
            f'Status: {escape(_availability_status(item))}',
            f'Terminy: {_dates_html(item)}',
        ]
        details = _training_details_html(item)
        entries.append(f'<li style="margin:0 0 12px;">{"<br>".join(lines)}{details}</li>')
    return '<ul style="margin:0;padding-left:20px;">' + "".join(entries) + "</ul>"


def render_trainings_text(items: list[Mapping[str, Any]], *, empty_message: str, heading: str = "Dostępne szkolenia:") -> str:
    if not items:
        return empty_message
    entries = [heading]
    for index, item in enumerate(items, start=1):
        entries.extend(
            [
                "",
                f"{index}. {item.get('name') or ''}",
                f"   Cena: {item.get('price_formatted') or format_price_pln(item.get('price'), item.get('currency'))}",
                f"   Limit miejsc: {_plain_display(item.get('capacity'))}",
                f"   Zajęte miejsca: {_plain_display(item.get('occupied_seats', 0))}",
                f"   Dostępne miejsca: {_plain_display(item.get('available_seats'))}",
                f"   Status: {_availability_status(item)}",
                f"   Terminy: {_dates_text(item)}",
            ]
        )
        if item.get("description"):
            entries.append(f"   Opis: {item['description']}")
        locations = _locations(item)
        if locations:
            entries.append(f"   Lokalizacja: {', '.join(locations)}")
        admin_comment = item.get("admin_comment") or item.get("low_seats_comment")
        if admin_comment:
            entries.append(f"   Komentarz administratora: {admin_comment}")
    return "\n".join(entries)


def _available_trainings(form, field, availability_service) -> list[dict[str, Any]]:
    if not field:
        return []
    if availability_service is not None:
        availability = availability_service.availability_for_field(
            form_slug=getattr(form, "slug", ""),
            field=field,
            current_submission_id="",
        )
        return TrainingCatalogService.get_trainings_for_field(
            field,
            availability,
            active_only=True,
        )
    catalog = TrainingCatalogService.get_trainings_for_field(
        field,
        active_only=True,
    )
    availability = build_training_availability(catalog, {})
    return TrainingCatalogService.get_trainings_for_field(
        field,
        availability,
        active_only=True,
    )


def _find_training_field(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        if value.get("type") == "training_selection" and isinstance(value.get("catalog"), list):
            return dict(value)
        for nested in value.values():
            result = _find_training_field(nested)
            if result:
                return result
    elif isinstance(value, list):
        for nested in value:
            result = _find_training_field(nested)
            if result:
                return result
    return None


def _training_details_html(item: Mapping[str, Any]) -> str:
    details = []
    if item.get("description"):
        details.append(f'Opis: {escape(str(item["description"]))}')
    locations = _locations(item)
    if locations:
        details.append(f'Lokalizacja: {escape(", ".join(locations))}')
    admin_comment = item.get("admin_comment") or item.get("low_seats_comment")
    if admin_comment:
        details.append(f'Komentarz administratora: {escape(str(admin_comment))}')
    return "".join(f"<br><small>{detail}</small>" for detail in details)


def _dates_html(item: Mapping[str, Any]) -> str:
    return escape(_dates_text(item))


def _dates_text(item: Mapping[str, Any]) -> str:
    labels = [str(date.get("label") or date.get("start_date") or "").strip() for date in item.get("dates") or []]
    return ", ".join(label for label in labels if label) or "Brak terminów"


def _locations(item: Mapping[str, Any]) -> list[str]:
    result = []
    direct = str(item.get("location") or "").strip()
    if direct:
        result.append(direct)
    for date in item.get("dates") or []:
        location = str(date.get("location") or "").strip()
        if location and location not in result:
            result.append(location)
    return result


def _availability_status(item: Mapping[str, Any]) -> str:
    if item.get("available_seats") == 0 or item.get("is_available") is False:
        return "Brak wolnych miejsc"
    if item.get("available_seats") is None:
        return "Dostępne — brak danych o limicie"
    return "Dostępne"


def _display(value: Any) -> str:
    return escape(_plain_display(value))


def _plain_display(value: Any) -> str:
    return "Brak danych" if value is None or value == "" else str(value)
