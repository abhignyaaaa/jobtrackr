"""Create tables if they don't exist. Runs once before the web server starts
(see the start command in render.yaml), so multiple workers never race."""
from __future__ import annotations

import time

from sqlalchemy import text

from . import models  # noqa: F401  (registers tables)
from .db import Base, engine


def main(retries: int = 10) -> None:
    import os
    if os.getenv("APP_ENV") == "production" and not os.getenv("DATABASE_URL"):
        raise SystemExit("DATABASE_URL is not set: add the database's Internal URL in the service's Environment settings")
    for attempt in range(1, retries + 1):
        try:
            with engine.begin() as conn:
                conn.execute(text("SELECT pg_advisory_xact_lock(424242)"))
                Base.metadata.create_all(conn)
            from .appsecret import ensure_secret_key
            ensure_secret_key()
            print("database ready")
            return
        except Exception as exc:  # DB may still be waking up on free tiers
            print(f"database not ready (attempt {attempt}/{retries}): {exc.__class__.__name__}")
            time.sleep(min(2 * attempt, 10))
    raise SystemExit("could not initialise database")


if __name__ == "__main__":
    main()
