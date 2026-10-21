from __future__ import annotations

import csv
import datetime as dt
import io
import math
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import asc, desc, func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..models import (
    COMP_PERIODS,
    EMPLOYMENT_TYPES,
    INTERVIEW_MODES,
    INTERVIEW_RESULTS,
    INTERVIEW_TYPES,
    STATUSES,
    Application,
    Interview,
    StatusChange,
)
from ..services.forms import CURRENCIES, parse_application, parse_interview
from ..web import check_csrf, local_today, redirect, render, require_user, user_tz

router = APIRouter()
PAGE_SIZE = 10
SORTS = {
    "updated": Application.updated_at,
    "applied": Application.applied_on,
    "deadline": Application.deadline,
    "company": func.lower(Application.company),
    "status": Application.status,
}


def get_owned(db: Session, user_id: int, app_id: int) -> Application:
    """Fetch an application only if it belongs to this user. Anything else is a
    404, so users cannot even learn whether another user's record exists."""
    app = db.scalar(select(Application).where(Application.id == app_id, Application.user_id == user_id))
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return app


def _choices():
    return dict(statuses=STATUSES, employment_types=EMPLOYMENT_TYPES, currencies=CURRENCIES,
                periods=COMP_PERIODS, interview_types=INTERVIEW_TYPES, interview_modes=INTERVIEW_MODES,
                interview_results=INTERVIEW_RESULTS)


def _filtered_query(user_id: int, params: dict):
    stmt = select(Application).where(Application.user_id == user_id)
    q = (params.get("q") or "").strip()[:100]
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = "%" + escaped + "%"
        stmt = stmt.where(or_(
            Application.company.ilike(like, escape="\\"), Application.title.ilike(like, escape="\\"),
            Application.location.ilike(like, escape="\\"), Application.notes.ilike(like, escape="\\"),
            Application.description.ilike(like, escape="\\"),
        ))
    if params.get("status") in STATUSES:
        stmt = stmt.where(Application.status == params["status"])
    if params.get("type") in EMPLOYMENT_TYPES:
        stmt = stmt.where(Application.employment_type == params["type"])
    company = (params.get("company") or "").strip()[:120]
    if company:
        stmt = stmt.where(func.lower(Application.company) == company.lower())
    for key, op in (("from", "ge"), ("to", "le")):
        raw = params.get(key) or ""
        if raw:
            try:
                d = dt.date.fromisoformat(raw)
                stmt = stmt.where(Application.applied_on >= d if op == "ge" else Application.applied_on <= d)
            except ValueError:
                pass
    return stmt


@router.get("/applications")
def list_applications(request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    p = dict(request.query_params)
    stmt = _filtered_query(user.id, p)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    sort = p.get("sort") if p.get("sort") in SORTS else "updated"
    direction = p.get("dir") if p.get("dir") in {"asc", "desc"} else "desc"
    col = SORTS[sort]
    order = (asc(col).nulls_last() if direction == "asc" else desc(col).nulls_last())
    pages = max(1, math.ceil(total / PAGE_SIZE))
    try:
        page = min(max(1, int(p.get("page", 1))), pages)
    except ValueError:
        page = 1
    rows = db.scalars(stmt.order_by(order, Application.id.desc()).limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)).all()
    companies = db.scalars(
        select(Application.company).where(Application.user_id == user.id).distinct().order_by(Application.company)
    ).all()

    def page_url(n: int) -> str:
        q = {k: v for k, v in p.items() if v and k != "page"}
        q["page"] = n
        return "/applications?" + urlencode(q)

    def sort_url(key: str) -> str:
        q = {k: v for k, v in p.items() if v and k not in {"page", "sort", "dir"}}
        q["sort"] = key
        q["dir"] = "asc" if (sort == key and direction == "desc") else "desc"
        return "/applications?" + urlencode(q)

    export_q = urlencode({k: v for k, v in p.items() if v and k not in {"page"}})
    return render(request, "applications/list.html", rows=rows, total=total, page=page, pages=pages,
                  page_url=page_url, sort_url=sort_url, sort=sort, direction=direction, params=p,
                  companies=companies, export_q=export_q, **_choices())


@router.get("/applications/export.csv")
def export_csv(request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    rows = db.scalars(_filtered_query(user.id, dict(request.query_params)).order_by(Application.id)).all()

    def cell(v):
        s = "" if v is None else str(v)
        # Neutralise spreadsheet formula injection.
        return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Company", "Title", "Status", "Type", "Location", "Applied on", "Deadline",
                "Comp min", "Comp max", "Currency", "Period", "Source", "Job URL", "Notes"])
    for a in rows:
        w.writerow([cell(x) for x in (a.company, a.title, a.status, a.employment_type, a.location, a.applied_on,
                                      a.deadline, a.comp_min, a.comp_max, a.comp_currency, a.comp_period,
                                      a.source, a.job_url, a.notes)])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="applications.csv"'})


@router.get("/applications/new")
def new_application(request: Request):
    user = require_user(request)
    return render(request, "applications/form.html", app=None, form={"status": "Saved", "comp_currency": "INR",
                  "comp_period": "year", "employment_type": "Internship"}, errors={}, **_choices())


def _apply(app: Application, data: dict, today: dt.date):
    for k, v in data.items():
        setattr(app, k, v)
    if app.status != "Saved" and app.applied_on is None:
        app.applied_on = today


@router.post("/applications")
async def create_application(request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    await check_csrf(request)
    form = await request.form()
    data, errors = parse_application(form)
    if errors:
        return render(request, "applications/form.html", status_code=400, app=None, form=dict(form),
                      errors=errors, **_choices())
    app = Application(user_id=user.id)
    _apply(app, data, local_today(user))
    app.history.append(StatusChange(from_status=None, to_status=app.status, note="Created"))
    db.add(app)
    db.commit()
    return redirect(f"/applications/{app.id}", "Application added.")


@router.get("/applications/{app_id}")
def show_application(app_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    app = db.scalar(
        select(Application).options(selectinload(Application.history), selectinload(Application.interviews),
                                    selectinload(Application.documents))
        .where(Application.id == app_id, Application.user_id == user.id)
    )
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return render(request, "applications/detail.html", app=app, form={}, errors={},
                  now_utc=dt.datetime.now(dt.timezone.utc), **_choices())


@router.get("/applications/{app_id}/edit")
def edit_application(app_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    app = get_owned(db, user.id, app_id)
    form = {c: getattr(app, c) for c in ("company", "title", "description", "job_url", "location", "employment_type",
                                         "source", "applied_on", "deadline", "comp_min", "comp_max", "comp_currency",
                                         "comp_period", "status", "notes")}
    form = {k: ("" if v is None else str(v)) for k, v in form.items()}
    return render(request, "applications/form.html", app=app, form=form, errors={}, **_choices())


@router.post("/applications/{app_id}/edit")
async def update_application(app_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    await check_csrf(request)
    app = get_owned(db, user.id, app_id)
    form = await request.form()
    data, errors = parse_application(form)
    if errors:
        return render(request, "applications/form.html", status_code=400, app=app, form=dict(form),
                      errors=errors, **_choices())
    old = app.status
    _apply(app, data, local_today(user))
    if app.status != old:
        app.history.append(StatusChange(from_status=old, to_status=app.status, note="Edited"))
    db.commit()
    return redirect(f"/applications/{app.id}", "Application updated.")


@router.post("/applications/{app_id}/status")
async def change_status(app_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    await check_csrf(request)
    app = get_owned(db, user.id, app_id)
    form = await request.form()
    new = str(form.get("status") or "")
    note = str(form.get("note") or "").strip()[:300] or None
    if new not in STATUSES:
        return redirect(f"/applications/{app.id}", "Choose a valid status.", "error")
    if new == app.status:
        return redirect(f"/applications/{app.id}", f"Status is already {new}.", "info")
    app.history.append(StatusChange(from_status=app.status, to_status=new, note=note))
    app.status = new
    if new != "Saved" and app.applied_on is None:
        app.applied_on = local_today(user)
    db.commit()
    return redirect(f"/applications/{app.id}", f"Status changed to {new}.")


@router.post("/applications/{app_id}/delete")
async def delete_application(app_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    await check_csrf(request)
    app = get_owned(db, user.id, app_id)
    name = f"{app.title} at {app.company}"
    db.delete(app)  # history and interviews cascade; documents are kept, unlinked
    db.commit()
    return redirect("/applications", f"Deleted {name}.")


# ---------- interviews ----------

@router.post("/applications/{app_id}/interviews")
async def add_interview(app_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    await check_csrf(request)
    app = get_owned(db, user.id, app_id)
    form = await request.form()
    data, errors = parse_interview(form, user_tz(user))
    if errors:
        return redirect(f"/applications/{app.id}#interviews", " ".join(errors.values()), "error")
    app.interviews.append(Interview(**data))
    if app.status in {"Saved", "Applied", "Assessment"}:
        app.history.append(StatusChange(from_status=app.status, to_status="Interview", note="Interview scheduled"))
        app.status = "Interview"
        if app.applied_on is None:
            app.applied_on = local_today(user)
    db.commit()
    return redirect(f"/applications/{app.id}#interviews", "Interview added.")


def get_owned_interview(db: Session, user_id: int, interview_id: int) -> Interview:
    iv = db.scalar(select(Interview).join(Application)
                   .where(Interview.id == interview_id, Application.user_id == user_id))
    if iv is None:
        raise HTTPException(status_code=404, detail="Interview not found")
    return iv


@router.get("/interviews")
def list_interviews(request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    now = dt.datetime.now(dt.timezone.utc)
    upcoming = db.execute(select(Interview, Application).join(Application)
                          .where(Application.user_id == user.id, Interview.scheduled_at >= now)
                          .order_by(Interview.scheduled_at)).all()
    past = db.execute(select(Interview, Application).join(Application)
                      .where(Application.user_id == user.id, Interview.scheduled_at < now)
                      .order_by(Interview.scheduled_at.desc()).limit(50)).all()
    return render(request, "interviews/list.html", upcoming=upcoming, past=past)


@router.get("/interviews/{iv_id}/edit")
def edit_interview(iv_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    iv = get_owned_interview(db, user.id, iv_id)
    local = iv.scheduled_at.astimezone(user_tz(user)).strftime("%Y-%m-%dT%H:%M")
    form = {"scheduled_at": local, "round_name": iv.round_name, "interview_type": iv.interview_type,
            "mode": iv.mode, "meeting_link": iv.meeting_link or "", "prep_notes": iv.prep_notes or "",
            "result": iv.result}
    return render(request, "interviews/form.html", iv=iv, form=form, errors={}, **_choices())


@router.post("/interviews/{iv_id}/edit")
async def update_interview(iv_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    await check_csrf(request)
    iv = get_owned_interview(db, user.id, iv_id)
    form = await request.form()
    data, errors = parse_interview(form, user_tz(user))
    if errors:
        return render(request, "interviews/form.html", status_code=400, iv=iv, form=dict(form),
                      errors=errors, **_choices())
    for k, v in data.items():
        setattr(iv, k, v)
    db.commit()
    return redirect(f"/applications/{iv.application_id}#interviews", "Interview updated.")


@router.post("/interviews/{iv_id}/delete")
async def delete_interview(iv_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    await check_csrf(request)
    iv = get_owned_interview(db, user.id, iv_id)
    app_id = iv.application_id
    db.delete(iv)
    db.commit()
    return redirect(f"/applications/{app_id}#interviews", "Interview deleted.")
