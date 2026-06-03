from __future__ import annotations

import os
from functools import lru_cache

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None

if load_dotenv:
    load_dotenv()


def get_database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def normalize_database_url(database_url: str) -> str:
    url = str(database_url or "").strip()
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def require_sqlalchemy():
    try:
        import sqlalchemy
        from sqlalchemy.orm import sessionmaker
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "DATABASE_URL is configured, but SQLAlchemy is not installed. "
            "Install project requirements before enabling PostgreSQL."
        ) from exc
    return sqlalchemy, sessionmaker


@lru_cache(maxsize=4)
def create_engine(database_url: str):
    sqlalchemy, _ = require_sqlalchemy()
    return sqlalchemy.create_engine(
        normalize_database_url(database_url),
        future=True,
        pool_pre_ping=True,
    )


@lru_cache(maxsize=4)
def create_session_factory(database_url: str):
    _, sessionmaker = require_sqlalchemy()
    return sessionmaker(bind=create_engine(database_url), expire_on_commit=False, future=True)
