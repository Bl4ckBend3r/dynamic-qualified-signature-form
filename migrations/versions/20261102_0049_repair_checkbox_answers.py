
"""Repair historical multi-checkbox answers.

Revision ID: 20261102_0049
Revises: 20260901_0048
"""

import json
import logging

import sqlalchemy as sa
from alembic import op


revision = "20261102_0049"
down_revision = "20260901_0048"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def parse_dict(value):
    if isinstance(value, dict):
        return dict(value)

    if isinstance(value, (str, bytes)):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else None
        except (ValueError, TypeError):
            return None

    return None


def get_fields(definition):
    fields = list(definition.get("fields") or [])

    documents = definition.get("documents") or []

    if isinstance(documents, dict):
        documents = documents.values()

    for document in documents:
        if isinstance(document, dict):
            fields.extend(document.get("fields") or [])

    return fields


def get_multi_checkbox_fields(definition):
    result = {}

    for field in get_fields(definition):
        if not isinstance(field, dict):
            continue

        if field.get("type") != "checkbox":
            continue

        options = field.get("options") or []

        if not isinstance(options, list) or len(options) <= 1:
            continue

        name = str(field.get("name") or "").strip()
        if not name:
            continue

        allowed = set()

        for option in options:
            if isinstance(option, dict):
                code = option.get("value")
                if code is None:
                    code = option.get("label", "")
            else:
                code = str(option).split("|", 1)[0]

            code = str(code).strip()
            if code:
                allowed.add(code)

        if len(allowed) == len(options):
            result[name] = allowed

    return result


def normalize_answer(value, allowed):
    if not isinstance(value, str):
        return None

    raw = value.strip()

    if not raw:
        return None

    # Pojedyncza poprawna opcja zapisana jako tekst.
    if raw in allowed:
        return [raw]

    # Lista opcji zapisana jako tekst JSON.
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None

    if not isinstance(parsed, list):
        return None

    if not all(
        isinstance(item, str) and item in allowed
        for item in parsed
    ):
        return None

    return parsed


def upgrade():
    connection = op.get_bind()
    metadata = sa.MetaData()

    submissions = sa.Table(
        "form_submissions",
        metadata,
        autoload_with=connection,
    )

    versions = sa.Table(
        "form_versions",
        metadata,
        autoload_with=connection,
    )

    # PostgreSQL używa JSON/JSONB.
    # MariaDB może odzwierciedlać JSON jako LONGTEXT.
    json_column = isinstance(
        submissions.c.data_json.type,
        sa.JSON,
    )

    last_id = 0
    scanned = 0
    repaired = 0
    requires_review = 0

    while True:
        query = (
            sa.select(
                submissions.c.id,
                submissions.c.data_json,
                versions.c.definition_json,
            )
            .select_from(
                submissions.outerjoin(
                    versions,
                    submissions.c.form_version_id == versions.c.id,
                )
            )
            .where(submissions.c.id > last_id)
            .order_by(submissions.c.id)
            .limit(200)
        )

        rows = connection.execute(query).mappings().all()

        if not rows:
            break

        for row in rows:
            last_id = row["id"]
            scanned += 1

            data = parse_dict(row["data_json"])
            definition = parse_dict(row["definition_json"])

            if data is None or definition is None:
                requires_review += 1
                continue

            modified = False

            for field_name, allowed in get_multi_checkbox_fields(
                definition
            ).items():

                if field_name not in data:
                    continue

                original = data[field_name]

                # Poprawnie zapisane listy pozostają bez zmian.
                if isinstance(original, list):
                    continue

                if original is None or original == "":
                    continue

                normalized = normalize_answer(
                    original,
                    allowed,
                )

                if normalized is None:
                    requires_review += 1
                    continue

                data[field_name] = normalized
                modified = True

            if not modified:
                continue

            payload = (
                data
                if json_column
                else json.dumps(data, ensure_ascii=False)
            )

            connection.execute(
                sa.update(submissions)
                .where(submissions.c.id == row["id"])
                .values(data_json=payload)
            )

            repaired += 1

    logger.info(
        "Checkbox repair: scanned=%d, repaired=%d, "
        "requires_review=%d",
        scanned,
        repaired,
        requires_review,
    )


def downgrade():
    raise RuntimeError(
        "Naprawy historycznych odpowiedzi nie można "
        "automatycznie cofnąć. Przywróć kopię bazy."
    )
