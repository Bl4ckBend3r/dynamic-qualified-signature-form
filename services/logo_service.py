from __future__ import annotations

import hashlib
import re
import secrets
from pathlib import Path

from sqlalchemy import select

from models import Logo, User
from services.upload_validation import validate_logo_upload


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


def list_logos_for_admin(db, user: User) -> list[Logo]:
    query = select(Logo).order_by(Logo.created_at.desc())
    if user.role != ROLE_SUPER_ADMIN:
        query = query.where(Logo.active.is_(True))
    return db.execute(query).scalars().all()


def can_select_logo(db, user: User, logo_id: int) -> bool:
    logo = db.get(Logo, logo_id)
    if not logo:
        return False
    return user.role == ROLE_SUPER_ADMIN or logo.active


def can_select_active_logo(db, logo_id: int) -> bool:
    logo = db.get(Logo, logo_id)
    return bool(logo and logo.active)


def create_logo_from_upload(
    db,
    *,
    temp_dir: str | Path,
    uploaded_filename: str,
    uploaded_bytes: bytes,
    uploaded_mimetype: str | None,
    name: str,
    uploaded_by_user_id: int,
) -> Logo:
    mime_type = validate_logo_upload(uploaded_filename, uploaded_bytes, uploaded_mimetype)
    logo_dir = Path(temp_dir) / "logos"
    logo_dir.mkdir(parents=True, exist_ok=True)
    safe_filename = f"{secrets.token_hex(8)}_{safe_asset_filename(uploaded_filename)}"
    storage_path = logo_dir / safe_filename
    storage_path.write_bytes(uploaded_bytes)
    logo = Logo(
        name=name.strip() or Path(uploaded_filename).stem,
        filename=Path(uploaded_filename).name,
        storage_path=str(storage_path),
        mime_type=mime_type,
        size_bytes=len(uploaded_bytes),
        checksum_sha256=hashlib.sha256(uploaded_bytes).hexdigest(),
        uploaded_by_user_id=uploaded_by_user_id,
        active=True,
    )
    db.add(logo)
    return logo


def update_logo_metadata(logo: Logo, *, name: str, active: bool) -> Logo:
    logo.name = name.strip() or logo.name
    logo.active = active
    return logo


def logo_asset_path_for_user(logo: Logo | None, user: User) -> Path | None:
    if not logo or (user.role != ROLE_SUPER_ADMIN and not logo.active):
        return None
    logo_path = Path(logo.storage_path)
    return logo_path if logo_path.exists() else None
