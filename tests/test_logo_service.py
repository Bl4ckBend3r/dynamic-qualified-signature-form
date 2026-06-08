import pytest

pytest.importorskip("sqlalchemy")

from database import create_engine, create_session_factory
from models import Base, Logo, User
from services.logo_service import can_select_active_logo, can_select_logo, list_active_logos, safe_asset_filename
from werkzeug.security import generate_password_hash


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
