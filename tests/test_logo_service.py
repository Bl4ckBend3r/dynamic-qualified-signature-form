import pytest
from pathlib import Path

pytest.importorskip("sqlalchemy")

from database import create_engine, create_session_factory
from models import Base, Logo, User
from services.logo_service import (
    can_select_active_logo,
    can_select_logo,
    create_logo_from_upload,
    list_active_logos,
    list_logos_for_admin,
    logo_asset_path_for_user,
    safe_asset_filename,
    update_logo_metadata,
)
from werkzeug.security import generate_password_hash


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_safe_asset_filename_removes_path_and_unsafe_chars():
    assert safe_asset_filename("../logo test!.png") == "logo_test_.png"


def test_logo_selection_helpers_keep_admin_rules(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'logos.db'}"
    Base.metadata.create_all(create_engine(database_url))
    session_factory = create_session_factory(database_url)

    with session_factory() as db:
        user = User(
            email="manager@example.com",
            password_hash=generate_password_hash("secret"),
            role="form_manager",
            is_active=True,
            is_blocked=False,
        )
        active = Logo(
            name="Active",
            filename="active.png",
            storage_path="tmp/logos/active.png",
            mime_type="image/png",
            size_bytes=1,
            checksum_sha256="a",
            active=True,
        )
        inactive = Logo(
            name="Inactive",
            filename="inactive.png",
            storage_path="tmp/logos/inactive.png",
            mime_type="image/png",
            size_bytes=1,
            checksum_sha256="b",
            active=False,
        )
        db.add_all([user, active, inactive])
        db.commit()

        assert can_select_logo(db, user, active.id)
        assert not can_select_logo(db, user, inactive.id)
        assert can_select_active_logo(db, active.id)
        assert not can_select_active_logo(db, inactive.id)
        assert [logo.name for logo in list_active_logos(db)] == ["Active"]


def test_logo_admin_helpers_keep_super_admin_and_manager_visibility(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'admin-logos.db'}"
    Base.metadata.create_all(create_engine(database_url))
    session_factory = create_session_factory(database_url)

    with session_factory() as db:
        super_admin = User(
            email="super@example.com",
            password_hash=generate_password_hash("secret"),
            role="super_admin",
            is_active=True,
            is_blocked=False,
        )
        manager = User(
            email="manager@example.com",
            password_hash=generate_password_hash("secret"),
            role="form_manager",
            is_active=True,
            is_blocked=False,
        )
        active_path = tmp_path / "active.png"
        active_path.write_bytes(PNG_BYTES)
        inactive_path = tmp_path / "inactive.png"
        inactive_path.write_bytes(PNG_BYTES)
        active = Logo(
            name="Active",
            filename="active.png",
            storage_path=str(active_path),
            mime_type="image/png",
            size_bytes=len(PNG_BYTES),
            checksum_sha256="a",
            active=True,
        )
        inactive = Logo(
            name="Inactive",
            filename="inactive.png",
            storage_path=str(inactive_path),
            mime_type="image/png",
            size_bytes=len(PNG_BYTES),
            checksum_sha256="b",
            active=False,
        )
        db.add_all([super_admin, manager, active, inactive])
        db.commit()

        assert {logo.name for logo in list_logos_for_admin(db, super_admin)} == {"Active", "Inactive"}
        assert [logo.name for logo in list_logos_for_admin(db, manager)] == ["Active"]
        assert logo_asset_path_for_user(active, manager) == active_path
        assert logo_asset_path_for_user(inactive, manager) is None
        assert logo_asset_path_for_user(inactive, super_admin) == inactive_path

        update_logo_metadata(active, name="  Changed  ", active=False)
        assert active.name == "Changed"
        assert active.active is False


def test_create_logo_from_upload_persists_metadata_and_file(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'upload-logos.db'}"
    Base.metadata.create_all(create_engine(database_url))
    session_factory = create_session_factory(database_url)

    with session_factory() as db:
        user = User(
            email="super@example.com",
            password_hash=generate_password_hash("secret"),
            role="super_admin",
            is_active=True,
            is_blocked=False,
        )
        db.add(user)
        db.commit()

        logo = create_logo_from_upload(
            db,
            temp_dir=tmp_path,
            uploaded_filename="logo test!.png",
            uploaded_bytes=PNG_BYTES,
            uploaded_mimetype="image/png",
            name="Uploaded",
            uploaded_by_user_id=user.id,
        )
        db.commit()

        assert logo.name == "Uploaded"
        assert logo.filename == "logo test!.png"
        assert logo.mime_type == "image/png"
        assert logo.size_bytes == len(PNG_BYTES)
        assert logo.checksum_sha256
        assert logo.uploaded_by_user_id == user.id
        assert logo.active is True
        assert Path(logo.storage_path).read_bytes() == PNG_BYTES
