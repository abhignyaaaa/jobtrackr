from __future__ import annotations

from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import security
from ..config import settings
from ..db import get_db
from ..models import User
from ..web import check_csrf, current_user, redirect, render, require_user

router = APIRouter()
TIMEZONES = sorted(tz for tz in available_timezones() if "/" in tz and not tz.startswith(("Etc/", "SystemV/")))


def _set_session_cookie(resp, token: str):
    resp.set_cookie(
        security.SESSION_COOKIE, token,
        max_age=settings.session_days * 86400,
        httponly=True, secure=settings.cookie_secure, samesite="lax", path="/",
    )


def _safe_next(target: str | None) -> str:
    # Only allow local redirects to prevent open-redirect abuse.
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return "/dashboard"


@router.get("/register")
def register_form(request: Request):
    if current_user(request):
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "auth/register.html", form={}, errors={})


@router.post("/register")
async def register(request: Request, db: Session = Depends(get_db)):
    await check_csrf(request)
    form = await request.form()
    full_name = str(form.get("full_name") or "").strip()[:121]
    email = security.normalize_email(str(form.get("email") or ""))[:255]
    password = str(form.get("password") or "")
    confirm = str(form.get("confirm_password") or "")
    errors: dict[str, str] = {}
    if not full_name:
        errors["full_name"] = "Name is required."
    elif len(full_name) > 120:
        errors["full_name"] = "Name must be at most 120 characters."
    if not security.EMAIL_RE.match(email) or len(email) > 254:
        errors["email"] = "Enter a valid email address."
    probs = security.password_problems(password, email)
    if probs:
        errors["password"] = " ".join(probs)
    elif password != confirm:
        errors["confirm_password"] = "Passwords do not match."
    if not errors and db.scalar(select(User.id).where(User.email == email)):
        errors["email"] = "An account with this email already exists. Try logging in."
    if errors:
        return render(request, "auth/register.html", status_code=400,
                      form={"full_name": full_name, "email": email}, errors=errors)
    user = User(email=email, full_name=full_name, password_hash=security.hash_password(password),
                timezone=settings.default_timezone)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(request, "auth/register.html", status_code=400,
                      form={"full_name": full_name, "email": email},
                      errors={"email": "An account with this email already exists. Try logging in."})
    token = security.create_session(db, user, request.headers.get("user-agent"))
    resp = redirect("/dashboard", f"Welcome, {user.full_name.split()[0]}! Your account is ready.")
    _set_session_cookie(resp, token)
    return resp


@router.get("/login")
def login_form(request: Request, next: str | None = None):
    if current_user(request):
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "auth/login.html", form={}, error=None, next=_safe_next(next))


@router.post("/login")
async def login(request: Request, db: Session = Depends(get_db)):
    await check_csrf(request)
    form = await request.form()
    email = security.normalize_email(str(form.get("email") or ""))[:254]
    password = str(form.get("password") or "")[:256]
    nxt = _safe_next(str(form.get("next") or ""))
    ip = security.client_ip(request)
    generic = "Incorrect email or password."

    if security.login_locked(db, email, ip):
        return render(request, "auth/login.html", status_code=429, form={"email": email}, next=nxt,
                      error="Too many failed attempts. Please wait 15 minutes and try again.")

    user = db.scalar(select(User).where(User.email == email)) if email else None
    ok = security.verify_password(password, user.password_hash if user else None)
    security.record_login(db, email, ip, ok)
    if not ok or user is None:
        return render(request, "auth/login.html", status_code=401, form={"email": email}, next=nxt, error=generic)

    # Fresh session on every login (prevents session fixation).
    security.destroy_session(db, request.cookies.get(security.SESSION_COOKIE))
    token = security.create_session(db, user, request.headers.get("user-agent"))
    resp = redirect(nxt, "Logged in.")
    _set_session_cookie(resp, token)
    return resp


@router.post("/logout")
async def logout(request: Request, db: Session = Depends(get_db)):
    await check_csrf(request)
    security.destroy_session(db, request.cookies.get(security.SESSION_COOKIE))
    resp = redirect("/login", "You have been logged out.")
    resp.delete_cookie(security.SESSION_COOKIE, path="/")
    return resp


@router.get("/settings")
def settings_page(request: Request):
    user = require_user(request)
    return render(request, "auth/settings.html", timezones=TIMEZONES, errors={}, current_tz=user.timezone)


@router.post("/settings/timezone")
async def update_timezone(request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    await check_csrf(request)
    tz = str((await request.form()).get("timezone") or "")
    if tz not in TIMEZONES:
        return redirect("/settings", "Choose a valid time zone.", "error")
    db_user = db.get(User, user.id)
    db_user.timezone = tz
    db.commit()
    return redirect("/settings", f"Time zone set to {tz}.")


@router.post("/settings/password")
async def change_password(request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    await check_csrf(request)
    form = await request.form()
    current = str(form.get("current_password") or "")
    new = str(form.get("new_password") or "")
    confirm = str(form.get("confirm_password") or "")
    db_user = db.get(User, user.id)
    errors = {}
    if not security.verify_password(current, db_user.password_hash):
        errors["current_password"] = "Current password is incorrect."
    probs = security.password_problems(new, db_user.email)
    if probs:
        errors["new_password"] = " ".join(probs)
    elif new != confirm:
        errors["confirm_password"] = "Passwords do not match."
    if errors:
        return render(request, "auth/settings.html", status_code=400, timezones=TIMEZONES,
                      errors=errors, current_tz=db_user.timezone)
    db_user.password_hash = security.hash_password(new)
    db.commit()
    # Sign out every other device; issue a new session for this one.
    security.destroy_all_sessions(db, db_user.id)
    token = security.create_session(db, db_user, request.headers.get("user-agent"))
    resp = redirect("/settings", "Password changed. Other devices have been signed out.")
    _set_session_cookie(resp, token)
    return resp
