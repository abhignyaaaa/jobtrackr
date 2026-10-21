from __future__ import annotations

import datetime as dt
import logging
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import security
from .config import settings
from .db import SessionLocal, engine
from .routes import applications, auth, dashboard, documents
from .services.metrics import reminder_count
from .web import BASE_DIR, FLASH_COOKIE, LoginRequired, local_today, render

log = logging.getLogger("jobtrackr")

app = FastAPI(
    title="JobTrackr",
    docs_url=None, redoc_url=None, openapi_url=None,  # no public API explorer in production
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)


@app.middleware("http")
async def session_and_headers(request: Request, call_next):
    path = request.url.path
    request.state.user = None
    request.state.session = None
    request.state.reminder_count = 0
    new_anon_csrf = None

    if not path.startswith("/static") and path != "/healthz":
        db = SessionLocal()
        try:
            sess = security.load_session(db, request.cookies.get(security.SESSION_COOKIE))
            if sess is not None:
                request.state.session = sess
                request.state.user = sess.user
                if request.method == "GET":
                    request.state.reminder_count = reminder_count(
                        db, sess.user_id, local_today(sess.user), dt.datetime.now(dt.timezone.utc))
        finally:
            db.close()
        anon = request.cookies.get(security.CSRF_COOKIE)
        if not anon or len(anon) > 100:
            anon = new_anon_csrf = security.new_csrf()
        request.state.anon_csrf = anon

    response = await call_next(request)

    if new_anon_csrf:
        response.set_cookie(security.CSRF_COOKIE, new_anon_csrf, httponly=True, samesite="strict",
                            secure=settings.cookie_secure, path="/")
    sets_flash = any(k == b"set-cookie" and v.startswith(FLASH_COOKIE.encode() + b"=")
                     for k, v in response.raw_headers)
    if getattr(request.state, "clear_flash", False) and not sets_flash:
        response.delete_cookie(FLASH_COOKIE, path="/")

    h = response.headers
    h.setdefault("Content-Security-Policy", CSP)
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if settings.cookie_secure:
        h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.state.user is not None and not path.startswith("/static"):
        h.setdefault("Cache-Control", "private, no-store")  # don't cache private pages
    return response


@app.exception_handler(LoginRequired)
async def login_required_handler(request: Request, exc: LoginRequired):
    target = request.url.path
    if request.url.query:
        target += "?" + request.url.query
    if request.method != "GET":
        target = "/dashboard"
    return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    titles = {403: "Forbidden", 404: "Not found", 405: "Method not allowed", 429: "Too many requests"}
    return render(request, "error.html", status_code=exc.status_code,
                  code=exc.status_code, title=titles.get(exc.status_code, "Error"),
                  message=exc.detail if exc.status_code in (403, 404) else "The request could not be completed.")


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    # e.g. /applications/abc - treat malformed IDs as not found.
    return render(request, "error.html", status_code=404, code=404, title="Not found",
                  message="That page does not exist.")


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return render(request, "error.html", status_code=500, code=500, title="Something went wrong",
                  message="An unexpected error occurred. Please try again.")


@app.get("/healthz", include_in_schema=False)
def healthz():
    """Used by Render's health checks: verifies the app can reach Postgres."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "database": "ok"}
    except Exception:
        log.exception("health check failed")
        return JSONResponse({"status": "degraded", "database": "unreachable"}, status_code=503)


app.include_router(dashboard.router)
app.include_router(auth.router)
app.include_router(applications.router)
app.include_router(documents.router)
