"""Dashboard metrics. Every number is computed from the user's rows in the
database at request time; nothing is hard-coded or cached.

Definitions (also shown on the dashboard):
- Total applications: every application record the user has, any status.
- Submitted: applications whose status ever moved past "Saved"
  (i.e. the status history contains any status other than "Saved").
- Applied this month: applications whose "applied on" date falls in the
  current calendar month in the user's time zone.
- Awaiting response: current status is "Applied" (submitted, no reply yet).
- Interview rate: submitted applications that ever reached "Interview" OR
  have at least one interview recorded, divided by submitted applications.
- Offer rate: submitted applications that ever reached "Offer Received",
  divided by submitted applications.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import and_, distinct, exists, func, or_, select
from sqlalchemy.orm import Session

from ..models import CLOSED_STATUSES, STATUSES, Application, Interview, StatusChange


def _count(db: Session, stmt) -> int:
    return int(db.scalar(stmt) or 0)


def dashboard_metrics(db: Session, user_id: int, today: dt.date, now_utc: dt.datetime) -> dict:
    mine = Application.user_id == user_id
    base = select(func.count()).select_from(Application).where(mine)

    total = _count(db, base)
    month_start = today.replace(day=1)
    next_month = (month_start + dt.timedelta(days=32)).replace(day=1)
    applied_this_month = _count(db, base.where(Application.applied_on >= month_start, Application.applied_on < next_month))
    awaiting = _count(db, base.where(Application.status == "Applied"))
    offers = _count(db, base.where(Application.status == "Offer Received"))
    rejected = _count(db, base.where(Application.status == "Rejected"))

    upcoming_interviews = _count(
        db,
        select(func.count()).select_from(Interview).join(Application)
        .where(mine, Interview.scheduled_at >= now_utc, Interview.result == "Pending"),
    )

    # --- conversion metrics, based on status history ---
    ever = lambda status: exists().where(  # noqa: E731
        StatusChange.application_id == Application.id, StatusChange.to_status == status
    )
    submitted_cond = exists().where(StatusChange.application_id == Application.id, StatusChange.to_status != "Saved")
    has_interview = exists().where(Interview.application_id == Application.id)

    submitted = _count(db, base.where(submitted_cond))
    reached_interview = _count(db, base.where(submitted_cond, or_(ever("Interview"), has_interview)))
    reached_offer = _count(db, base.where(submitted_cond, ever("Offer Received")))

    def pct(n: int, d: int) -> float | None:
        return round(100.0 * n / d, 1) if d else None

    # --- status distribution ---
    rows = db.execute(
        select(Application.status, func.count()).where(mine).group_by(Application.status)
    ).all()
    by_status = {s: 0 for s in STATUSES}
    for status, n in rows:
        by_status[status] = int(n)

    # --- activity: applications submitted per week, last 12 weeks ---
    week_start = today - dt.timedelta(days=today.weekday())  # Monday
    first_week = week_start - dt.timedelta(weeks=11)
    applied_dates = db.scalars(
        select(Application.applied_on).where(mine, Application.applied_on >= first_week, Application.applied_on <= today)
    ).all()
    weeks = [first_week + dt.timedelta(weeks=i) for i in range(12)]
    counts = [0] * 12
    for d in applied_dates:
        idx = (d - first_week).days // 7
        if 0 <= idx < 12:
            counts[idx] += 1

    return {
        "total": total,
        "applied_this_month": applied_this_month,
        "awaiting": awaiting,
        "upcoming_interviews": upcoming_interviews,
        "offers": offers,
        "rejected": rejected,
        "submitted": submitted,
        "reached_interview": reached_interview,
        "reached_offer": reached_offer,
        "interview_rate": pct(reached_interview, submitted),
        "offer_rate": pct(reached_offer, submitted),
        "by_status": by_status,
        "activity": {
            "labels": [w.strftime("%d %b") for w in weeks],
            "counts": counts,
        },
    }


def upcoming_items(db: Session, user_id: int, today: dt.date, now_utc: dt.datetime, days: int = 14):
    """Upcoming interviews and approaching deadlines for the reminders panel."""
    horizon_utc = now_utc + dt.timedelta(days=days)
    interviews = db.execute(
        select(Interview, Application)
        .join(Application)
        .where(Application.user_id == user_id, Interview.scheduled_at >= now_utc,
               Interview.scheduled_at <= horizon_utc, Interview.result == "Pending")
        .order_by(Interview.scheduled_at)
        .limit(20)
    ).all()
    deadlines = db.scalars(
        select(Application)
        .where(Application.user_id == user_id, Application.deadline >= today,
               Application.deadline <= today + dt.timedelta(days=days),
               Application.status.not_in(CLOSED_STATUSES))
        .order_by(Application.deadline)
        .limit(20)
    ).all()
    overdue = db.scalars(
        select(Application)
        .where(Application.user_id == user_id, Application.deadline < today,
               Application.deadline >= today - dt.timedelta(days=30), Application.status == "Saved")
        .order_by(Application.deadline.desc())
        .limit(10)
    ).all()
    return interviews, deadlines, overdue


def reminder_count(db: Session, user_id: int, today: dt.date, now_utc: dt.datetime) -> int:
    """Items needing attention within 48 hours (nav badge)."""
    soon = now_utc + dt.timedelta(hours=48)
    n_int = db.scalar(
        select(func.count()).select_from(Interview).join(Application)
        .where(Application.user_id == user_id, Interview.scheduled_at >= now_utc,
               Interview.scheduled_at <= soon, Interview.result == "Pending")
    ) or 0
    n_dl = db.scalar(
        select(func.count()).select_from(Application)
        .where(Application.user_id == user_id, Application.deadline >= today,
               Application.deadline <= today + dt.timedelta(days=2),
               Application.status.not_in(CLOSED_STATUSES))
    ) or 0
    return int(n_int) + int(n_dl)
