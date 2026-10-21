"""Application settings, read once from environment variables.

Nothing secret is hard-coded: production values come from the hosting
platform's environment (Render dashboard / render.yaml).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_db_url(url: str) -> str:
    # Render / Neon hand out "postgres://" or "postgresql://" URLs; SQLAlchemy
    # needs the driver spelled out to use psycopg 3.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


@dataclass(frozen=True)
class Settings:
    env: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    database_url: str = field(
        default_factory=lambda: _normalize_db_url(
            os.getenv("DATABASE_URL", "postgresql://jt:jt_local_pw@localhost:5432/jobtracker")
        )
    )
    # Signs flash-message cookies. If not provided via the environment, the app
    # generates a random key once and stores it in the database (app/appsecret.py),
    # so every worker and every restart shares it and no human handles it.
    secret_key: str | None = field(default_factory=lambda: os.getenv("SECRET_KEY") or None)
    session_days: int = field(default_factory=lambda: int(os.getenv("SESSION_DAYS", "7")))
    session_idle_hours: int = field(default_factory=lambda: int(os.getenv("SESSION_IDLE_HOURS", "48")))
    # Cookies are Secure-only unless explicitly running local HTTP dev.
    cookie_secure: bool = field(default_factory=lambda: _bool("COOKIE_SECURE", os.getenv("APP_ENV", "development") == "production"))
    default_timezone: str = field(default_factory=lambda: os.getenv("DEFAULT_TIMEZONE", "Asia/Kolkata"))

    # Document storage: "disabled" | "local" | "s3"
    storage_backend: str = field(default_factory=lambda: os.getenv("STORAGE_BACKEND", "disabled").lower())
    local_storage_dir: str = field(default_factory=lambda: os.getenv("LOCAL_STORAGE_DIR", "/tmp/jobtrackr-uploads"))
    s3_bucket: str = field(default_factory=lambda: os.getenv("S3_BUCKET", ""))
    s3_endpoint_url: str | None = field(default_factory=lambda: os.getenv("S3_ENDPOINT_URL") or None)
    s3_region: str = field(default_factory=lambda: os.getenv("S3_REGION", "auto"))
    s3_access_key_id: str | None = field(default_factory=lambda: os.getenv("S3_ACCESS_KEY_ID") or None)
    s3_secret_access_key: str | None = field(default_factory=lambda: os.getenv("S3_SECRET_ACCESS_KEY") or None)
    max_upload_mb: int = field(default_factory=lambda: int(os.getenv("MAX_UPLOAD_MB", "5")))
    max_docs_per_user: int = field(default_factory=lambda: int(os.getenv("MAX_DOCS_PER_USER", "20")))

    @property
    def is_production(self) -> bool:
        return self.env == "production"


settings = Settings()
