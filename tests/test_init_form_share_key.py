from __future__ import annotations

import os
import stat
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from models import Base, Form
from scripts.deployment.init_form_share_key import ensure_form_share_key
from services import form_access_service


@pytest.fixture
def installation(tmp_path, monkeypatch):
    monkeypatch.delenv("FORM_SHARE_TOKEN_ENCRYPTION_KEY", raising=False)
    db_path = tmp_path / "existing.sqlite"
    database_url = f"sqlite:///{db_path.as_posix()}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    env_file = tmp_path / ".env"
    env_file.write_text(f"DATABASE_URL={database_url}\nSECRET_KEY=keep-me\n", encoding="utf-8")
    monkeypatch.setattr(
        form_access_service,
        "__file__",
        str(tmp_path / "services" / "form_access_service.py"),
    )
    yield env_file, database_url, engine
    engine.dispose()


def _stored_key(env_file):
    return next(
        line.split("=", 1)[1]
        for line in env_file.read_text(encoding="utf-8").splitlines()
        if line.startswith("FORM_SHARE_TOKEN_ENCRYPTION_KEY=")
    )


def _insert_form(engine, *, encrypted=None, token_hash=""):
    with Session(engine) as db:
        db.add(Form(slug="shared", name="Shared", share_token_encrypted=encrypted, share_token_hash=token_hash))
        db.commit()


def test_upgrade_without_key_persists_once_and_preserves_settings(installation):
    env_file, database_url, _ = installation
    original = env_file.read_bytes()

    assert "utworzono" in ensure_form_share_key(env_file, database_url=database_url)
    key = _stored_key(env_file)
    Fernet(key.encode("ascii"))
    assert env_file.read_bytes().startswith(original)

    saved = env_file.read_bytes()
    assert "zachowano" in ensure_form_share_key(env_file, database_url=database_url)
    assert env_file.read_bytes() == saved
    if os.name != "nt":
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_existing_key_survives_upgrade_and_restart_and_decrypts_link(installation):
    env_file, database_url, engine = installation
    key = Fernet.generate_key().decode("ascii")
    token = "already-shared-link"
    encrypted = Fernet(key.encode("ascii")).encrypt(token.encode()).decode()
    _insert_form(engine, encrypted=encrypted, token_hash=form_access_service.hash_share_token(token))
    env_file.write_text(env_file.read_text() + f"FORM_SHARE_TOKEN_ENCRYPTION_KEY={key}\n")
    before = env_file.read_bytes()

    assert "zachowano" in ensure_form_share_key(env_file, database_url=database_url)
    assert "zachowano" in ensure_form_share_key(env_file, database_url=database_url)
    assert env_file.read_bytes() == before
    for _ in range(2):  # independent service reads model a restart
        assert form_access_service.decrypt_share_token(encrypted) == token
    with Session(engine) as db:
        form = db.execute(select(Form).where(Form.slug == "shared")).scalar_one()
        assert form.share_token_hash == form_access_service.hash_share_token(token)
        assert form.share_token_encrypted == encrypted


def test_environment_key_is_persisted_and_conflict_is_rejected(installation, monkeypatch):
    env_file, database_url, engine = installation
    key = Fernet.generate_key().decode("ascii")
    sample = Fernet(key.encode("ascii")).encrypt(b"link").decode("ascii")
    _insert_form(engine, encrypted=sample, token_hash=form_access_service.hash_share_token("link"))
    monkeypatch.setenv("FORM_SHARE_TOKEN_ENCRYPTION_KEY", key)

    assert "środowiska" in ensure_form_share_key(env_file, database_url=database_url)
    assert _stored_key(env_file) == key
    assert form_access_service.decrypt_share_token(sample) == "link"
    assert "zachowano" in ensure_form_share_key(env_file, database_url=database_url)

    monkeypatch.setenv("FORM_SHARE_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    before = env_file.read_bytes()
    with pytest.raises(RuntimeError, match="Konflikt"):
        ensure_form_share_key(env_file, database_url=database_url)
    assert env_file.read_bytes() == before


def test_missing_key_with_encrypted_links_fails_closed(installation):
    env_file, database_url, engine = installation
    encrypted = Fernet(Fernet.generate_key()).encrypt(b"existing-link").decode()
    _insert_form(engine, encrypted=encrypted)
    before = env_file.read_bytes()

    with pytest.raises(RuntimeError, match="Przywróć oryginalny"):
        ensure_form_share_key(env_file, database_url=database_url)
    assert env_file.read_bytes() == before


def test_invalid_key_is_not_replaced(installation):
    env_file, database_url, _ = installation
    env_file.write_text(env_file.read_text() + "FORM_SHARE_TOKEN_ENCRYPTION_KEY=invalid\n")
    before = env_file.read_bytes()

    with pytest.raises(RuntimeError, match="Niepoprawny"):
        ensure_form_share_key(env_file, database_url=database_url)
    assert env_file.read_bytes() == before


def test_valid_but_wrong_key_cannot_replace_missing_original(installation, monkeypatch):
    env_file, database_url, engine = installation
    original_key = Fernet.generate_key()
    encrypted = Fernet(original_key).encrypt(b"existing-link").decode()
    _insert_form(engine, encrypted=encrypted)
    monkeypatch.setenv("FORM_SHARE_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    before = env_file.read_bytes()

    with pytest.raises(RuntimeError, match="nie odszyfrowuje"):
        ensure_form_share_key(env_file, database_url=database_url)
    assert env_file.read_bytes() == before


def test_concurrent_initializers_keep_one_key(installation):
    env_file, database_url, _ = installation
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: ensure_form_share_key(env_file, database_url=database_url), range(2)))

    assert len([result for result in results if "utworzono" in result]) == 1
    assert env_file.read_text().count("FORM_SHARE_TOKEN_ENCRYPTION_KEY=") == 1
