from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import STATUSES
from ..services.metrics import dashboard_metrics, upcoming_items
from ..web import current_user, local_today, render, require_user

router = APIRouter()


@router.get("/")
def home(request: Request):
    if current_user(request):
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "landing.html")


@router.get("/dashboard")
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    today = local_today(user)
    now = dt.datetime.now(dt.timezone.utc)
    m = dashboard_metrics(db, user.id, today, now)
    interviews, deadlines, overdue = upcoming_items(db, user.id, today, now)
    chart_data = {
        "status": {"labels": STATUSES, "counts": [m["by_status"][s] for s in STATUSES]},
        "activity": m["activity"],
    }
    return render(request, "dashboard.html", m=m, chart_data=chart_data,
                  upcoming_interviews=interviews, deadlines=deadlines, overdue=overdue)
