from __future__ import annotations

from models import ContactPage


DEFAULT_CONTACT_ADDRESS = "ul. Podgórna 7\n65-057 Zielona Góra"
DEFAULT_CONTACT_EMAIL = "wnioski@lubuskie.pl"
DEFAULT_CONTACT_PHONES = [
    {"label": "tel.", "number": "+48 68 45 65 590"},
    {"label": "tel.", "number": "+48 68 45 65 591"},
    {"label": "fax", "number": "+48 68 45 65 468"},
]


def default_contact_page() -> ContactPage:
    return ContactPage(
        title="Kontakt",
        address=DEFAULT_CONTACT_ADDRESS,
        email=DEFAULT_CONTACT_EMAIL,
        phones=list(DEFAULT_CONTACT_PHONES),
    )


def ensure_contact_defaults(page: ContactPage) -> ContactPage:
    if not page.title:
        page.title = "Kontakt"
    if not normalized_phones(page.phones):
        migrated = []
        if page.phone:
            migrated.append({"label": "tel.", "number": page.phone})
        page.phones = migrated
    return page


def normalized_phones(value) -> list[dict[str, str]]:
    phones = []
    if not isinstance(value, list):
        return phones
    for item in value:
        if isinstance(item, dict):
            label = str(item.get("label") or "").strip()
            number = str(item.get("number") or "").strip()
        else:
            label = ""
            number = str(item or "").strip()
        if number:
            phones.append({"label": label, "number": number})
    return phones


def phones_from_form(labels: list[str], numbers: list[str]) -> list[dict[str, str]]:
    phones = []
    max_len = max(len(labels), len(numbers))
    for index in range(max_len):
        label = labels[index].strip() if index < len(labels) else ""
        number = numbers[index].strip() if index < len(numbers) else ""
        if number:
            phones.append({"label": label, "number": number})
    return phones
