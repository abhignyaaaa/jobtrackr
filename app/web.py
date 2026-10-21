"""Request-scoped helpers: current user, CSRF checks, flash messages, templates."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import User
from . import security

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
_flash_signer = URLSafeSerializer(settings.secret_key, salt="flash")
FLASH_COOKIE = "jt_flash"


class LoginRequired(Exception):
    """Raised by require_user; turned into a redirect to /login."""


# ---------- current user ----------

def current_user(request: Request) -> User | None:
    return getattr(request.state, "user", None)


def require_user(request: Request) -> User:
    user = current_user(request)
    if user is None:
        raise LoginRequired()
    return user


# ---------- CSRF ----------

async def check_csrf(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin != "null":
        if urlparse(origin).netloc != request.headers.get("host", ""):
            raise HTTPException(status_code=403, detail="Cross-origin request blocked")
    form = await request.form()
    sent = form.get("csrf_token")
    sess = getattr(request.state, "session", None)
    expected = sess.csrf_token if sess else request.cookies.get(security.CSRF_COOKIE)
    if not security.tokens_match(sent if isinstance(sent, str) else None, expected):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token. Reload the page and try again.")


def csrf_for(request: Request) -> str:
    sess = getattr(request.state, "session", None)
    if sess:
        return sess.csrf_token
    return request.state.anon_csrf


# ---------- flash ----------

def flash(response, message: str, kind: str = "success"):
    response.set_cookie(
        FLASH_COOKIE, _flash_signer.dumps({"m": message[:300], "k": kind}),
        max_age=60, httponly=True, samesite="lax", secure=settings.cookie_secure, path="/",
    )
    return response


def pop_flash(request: Request) -> dict | None:
    raw = request.cookies.get(FLASH_COOKIE)
    if not raw:
        return None
    try:
        data = _flash_signer.loads(raw)
    except BadSignature:
        return None
    request.state.clear_flash = True
    return data if isinstance(data, dict) else None


def redirect(url: str, message: str | None = None, kind: str = "success") -> RedirectResponse:
    resp = RedirectResponse(url, status_code=303)
    if message:
        flash(resp, message, kind)
    return resp


# ---------- time helpers ----------

def user_tz(user: User | None) -> ZoneInfo:
    try:
        return ZoneInfo(user.timezone if user else settings.default_timezone)
    except Exception:
        return ZoneInfo(settings.default_timezone)


def local_today(user: User | None) -> dt.date:
    return dt.datetime.now(user_tz(user)).date()


def render(request: Request, template: str, status_code: int = 200, **ctx):
    user = current_user(request)
    tz = user_tz(user)

    def localtime(value: dt.datetime | None, fmt: str = "%d %b %Y, %I:%M %p"):
        if value is None:
            return ""
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(tz).strftime(fmt)

    ctx.update(
        request=request,
        user=user,
        csrf_token=csrf_for(request),
        flash=pop_flash(request),
        localtime=localtime,
        today=local_today(user),
        tzname=str(tz),
        storage_enabled=settings.storage_backend in {"local", "s3"},
        reminder_count=getattr(request.state, "reminder_count", 0),
    )
    return templates.TemplateResponse(request, template, ctx, status_code=status_code)


def to_json(data) -> str:
    # Safe for embedding inside <script type="application/json">.
    return json.dumps(data).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


templates.env.filters["tojson_safe"] = to_json


def db_dep():
    return Depends(get_db)
