"""Cookie-signing key: from SECRET_KEY if set, otherwise generated once and
kept in the database so all workers and restarts share the same value."""
from __future__ import annotations

import secrets

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from .config import settings
from .db import SessionLocal
from .models import AppSecret

_cached: str | None = None


def ensure_secret_key() -> str:
    with SessionLocal() as db:
        db.execute(insert(AppSecret).values(name="secret_key", value=secrets.token_urlsafe(48))
                   .on_conflict_do_nothing(index_elements=["name"]))
        db.commit()
        return db.scalar(select(AppSecret.value).where(AppSecret.name == "secret_key"))


def get_secret_key() -> str:
    global _cached
    if _cached is None:
        _cached = settings.secret_key or ensure_secret_key()
    return _cached
