"""Password hashing, session tokens, CSRF and login rate limiting."""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import re
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import settings
from .models import LoginAttempt, User, UserSession

SESSION_COOKIE = "jt_session"
CSRF_COOKIE = "jt_csrf"

# Argon2id with library defaults (memory-hard; OWASP-recommended).
_ph = PasswordHasher()
# A fixed hash used to equalise timing when the email does not exist,
# so response time does not reveal which emails are registered.
_DUMMY_HASH = _ph.hash("not-a-real-password-" + secrets.token_hex(8))

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")

MAX_FAILS_PER_EMAIL_IP = 5
MAX_FAILS_PER_IP = 30
LOCK_WINDOW = dt.timedelta(minutes=15)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ---------- passwords ----------

def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str | None) -> bool:
    try:
        return _ph.verify(hashed or _DUMMY_HASH, password) and hashed is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_problems(password: str, email: str = "") -> list[str]:
    problems = []
    if len(password) < 8:
        problems.append("Password must be at least 8 characters.")
    if len(password) > 128:
        problems.append("Password must be at most 128 characters.")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        problems.append("Password must contain at least one letter and one number.")
    if email and password.lower() == email.lower():
        problems.append("Password must not be your email address.")
    return problems


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


# ---------- sessions ----------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User, user_agent: str | None) -> str:
    token = secrets.token_urlsafe(32)
    now = utcnow()
    db.add(
        UserSession(
            token_hash=_hash_token(token),
            user_id=user.id,
            csrf_token=secrets.token_urlsafe(32),
            created_at=now,
            last_seen_at=now,
            expires_at=now + dt.timedelta(days=settings.session_days),
            user_agent=(user_agent or "")[:255],
        )
    )
    # Opportunistic cleanup of this user's expired sessions.
    db.execute(delete(UserSession).where(UserSession.user_id == user.id, UserSession.expires_at < now))
    db.commit()
    return token


def load_session(db: Session, token: str | None) -> UserSession | None:
    if not token or len(token) > 100:
        return None
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == _hash_token(token)))
    if sess is None:
        return None
    now = utcnow()
    idle_limit = dt.timedelta(hours=settings.session_idle_hours)
    if sess.expires_at < now or now - sess.last_seen_at > idle_limit:
        db.delete(sess)
        db.commit()
        return None
    # Only write last_seen every few minutes to avoid a write per request.
    if now - sess.last_seen_at > dt.timedelta(minutes=5):
        sess.last_seen_at = now
        db.commit()
    return sess


def destroy_session(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(UserSession).where(UserSession.token_hash == _hash_token(token)))
        db.commit()


def destroy_all_sessions(db: Session, user_id: int) -> None:
    db.execute(delete(UserSession).where(UserSession.user_id == user_id))
    db.commit()


# ---------- CSRF ----------
# Anonymous forms (login/register) use a double-submit cookie.
# Authenticated forms use a per-session synchronizer token stored server-side.

def new_csrf() -> str:
    return secrets.token_urlsafe(32)


def tokens_match(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(str(a), str(b))


# ---------- rate limiting ----------

def client_ip(request: Request) -> str:
    # Uvicorn runs with --proxy-headers behind Render's load balancer,
    # so request.client.host is already the real client address.
    return (request.client.host if request.client else "unknown")[:64]


def login_locked(db: Session, email: str, ip: str) -> bool:
    since = utcnow() - LOCK_WINDOW
    fails_pair = db.scalar(
        select(func.count()).select_from(LoginAttempt).where(
            LoginAttempt.email == email, LoginAttempt.ip == ip,
            LoginAttempt.success.is_(False), LoginAttempt.attempted_at > since,
        )
    )
    fails_ip = db.scalar(
        select(func.count()).select_from(LoginAttempt).where(
            LoginAttempt.ip == ip, LoginAttempt.success.is_(False), LoginAttempt.attempted_at > since,
        )
    )
    return (fails_pair or 0) >= MAX_FAILS_PER_EMAIL_IP or (fails_ip or 0) >= MAX_FAILS_PER_IP


def record_login(db: Session, email: str, ip: str, success: bool) -> None:
    db.add(LoginAttempt(email=email[:254], ip=ip, success=success))
    if success:
        # Successful login clears the counter for this email+IP pair.
        db.execute(delete(LoginAttempt).where(
            LoginAttempt.email == email, LoginAttempt.ip == ip, LoginAttempt.success.is_(False)))
    # Keep the table small.
    db.execute(delete(LoginAttempt).where(LoginAttempt.attempted_at < utcnow() - dt.timedelta(days=2)))
    db.commit()
