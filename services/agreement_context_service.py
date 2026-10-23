from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Mapping

from services.training_service import format_price_pln, parse_decimal_price


LEGACY_TOTAL_PLACEHOLDER = re.compile(
    r"{{\s*selected_trainings_total_formatted\s*}}"
)
ALL_TRAININGS_TOTAL_PLACEHOLDER = (
    "{{ all_selected_trainings_total_formatted"
    "|default(selected_trainings_total_formatted, true) }}"
)


def build_training_agreement_value_context(
    all_selected_trainings: list[Mapping[str, Any]],
    agreement_training: Mapping[str, Any],
) -> dict[str, Any]:
    all_trainings = [dict(training) for training in all_selected_trainings]
    total = sum(
        (parse_decimal_price(training.get("price")) or Decimal("0") for training in all_trainings),
        Decimal("0"),
    )
    agreement_price = parse_decimal_price(agreement_training.get("price")) or Decimal("0")
    currency = str(agreement_training.get("currency") or "PLN")
    return {
        "agreement_training_price": agreement_price,
        "agreement_training_price_formatted": (
            agreement_training.get("price_formatted")
            or format_price_pln(agreement_price, currency)
        ),
        "all_selected_trainings": all_trainings,
        "all_selected_trainings_total": total,
        "all_selected_trainings_total_formatted": format_price_pln(total, currency),
    }


def upgrade_training_agreement_total_placeholder(
    template_html: str,
    *,
    show_all_trainings_total: bool = True,
) -> str:
    """Make legacy admin templates use the whole-submission total without changing their table loop."""
    if not show_all_trainings_total:
        return template_html
    if "all_selected_trainings_total_formatted" in template_html:
        return template_html
    return LEGACY_TOTAL_PLACEHOLDER.sub(ALL_TRAININGS_TOTAL_PLACEHOLDER, template_html)
