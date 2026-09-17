"""
Engine + session factory. SQLite by default (zero setup for local dev/demo),
Postgres in prod via DATABASE_URL — no code changes needed, SQLAlchemy
handles the dialect switch.
"""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models.db_models import Base

settings = get_settings()


def _normalize_db_url(url: str) -> str:
    """Cloud providers (Render, Heroku, etc.) commonly hand out
    'postgres://...' connection strings, but SQLAlchemy + the psycopg3
    driver need the explicit 'postgresql+psycopg://' scheme. Rewriting here
    means the .env / dashboard-provided URL can be pasted as-is."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://") and "+psycopg" not in url:
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


_db_url = _normalize_db_url(settings.database_url)
_connect_args = {"check_same_thread": False} if _db_url.startswith("sqlite") else {}

engine = create_engine(_db_url, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    """Create tables if they don't exist. Fine for demo/dev; use Alembic
    migrations for real schema evolution in prod."""
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency — one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()