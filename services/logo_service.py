from __future__ import annotations

import re
import secrets
from pathlib import Path

from sqlalchemy import select

from models import Logo, User


ROLE_SUPER_ADMIN = "super_admin"


def safe_asset_filename(value: str) -> str:
    filename = Path(value or "asset").name
    filename = re.sub(r"[^a-zA-Z0-9_.-]+", "_", filename).strip("._")
    return filename or f"asset_{secrets.token_hex(4)}"


def list_selectable_logos(db, user: User, current_logo_id: int | None = None) -> list[Logo]:
    query = select(Logo).order_by(Logo.name)
    if user.role != ROLE_SUPER_ADMIN:
        query = query.where(Logo.active.is_(True))
    logos = db.execute(query).scalars().all()
    if current_logo_id and current_logo_id not in {logo.id for logo in logos}:
        current = db.get(Logo, current_logo_id)
        if current and user.role == ROLE_SUPER_ADMIN:
            logos.append(current)
    return logos


def list_active_logos(db) -> list[Logo]:
    return db.execute(select(Logo).where(Logo.active.is_(True)).order_by(Logo.name)).scalars().all()


def can_select_logo(db, user: User, logo_id: int) -> bool:
    logo = db.get(Logo, logo_id)
    if not logo:
        return False
    return user.role == ROLE_SUPER_ADMIN or logo.active


def can_select_active_logo(db, logo_id: int) -> bool:
    logo = db.get(Logo, logo_id)
    return bool(logo and logo.active)
