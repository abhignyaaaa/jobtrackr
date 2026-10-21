"""Server-side validation for application and interview forms.

Every value coming from a browser is treated as untrusted: lengths are capped,
enums are whitelisted, URLs must be http(s), and dates/numbers are parsed
explicitly. The same rules run whether or not the browser validated first.
"""
from __future__ import annotations

import datetime as dt
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from ..models import (
    COMP_PERIODS,
    EMPLOYMENT_TYPES,
    INTERVIEW_MODES,
    INTERVIEW_RESULTS,
    INTERVIEW_TYPES,
    STATUSES,
)

CURRENCIES = ["INR", "USD", "EUR", "GBP"]


def _s(form, key: str, max_len: int) -> str:
    val = form.get(key)
    if not isinstance(val, str):
        return ""
    return val.strip()[: max_len + 1]


def _opt(form, key: str, max_len: int, errors: dict, label: str) -> str | None:
    val = _s(form, key, max_len)
    if len(val) > max_len:
        errors[key] = f"{label} must be at most {max_len} characters."
    return val or None


def _req(form, key: str, max_len: int, errors: dict, label: str) -> str:
    val = _s(form, key, max_len)
    if not val:
        errors[key] = f"{label} is required."
    elif len(val) > max_len:
        errors[key] = f"{label} must be at most {max_len} characters."
    return val


def _url(form, key: str, errors: dict, label: str) -> str | None:
    val = _s(form, key, 2048)
    if not val:
        return None
    parsed = urlparse(val)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or len(val) > 2048:
        errors[key] = f"{label} must be a valid http(s) link."
    return val


def _date(form, key: str, errors: dict, label: str) -> dt.date | None:
    val = _s(form, key, 10)
    if not val:
        return None
    try:
        d = dt.date.fromisoformat(val)
    except ValueError:
        errors[key] = f"{label} must be a valid date."
        return None
    if not (dt.date(2000, 1, 1) <= d <= dt.date(2100, 12, 31)):
        errors[key] = f"{label} is out of range."
    return d


def _int(form, key: str, errors: dict, label: str) -> int | None:
    val = _s(form, key, 15).replace(",", "")
    if not val:
        return None
    try:
        n = int(float(val))
    except ValueError:
        errors[key] = f"{label} must be a number."
        return None
    if n < 0 or n > 10**12:
        errors[key] = f"{label} is out of range."
    return n


def _choice(form, key: str, choices: list[str], default: str, errors: dict, label: str) -> str:
    val = _s(form, key, 40) or default
    if val not in choices:
        errors[key] = f"Choose a valid {label.lower()}."
        return default
    return val


def parse_application(form) -> tuple[dict, dict]:
    e: dict[str, str] = {}
    data = {
        "company": _req(form, "company", 120, e, "Company"),
        "title": _req(form, "title", 160, e, "Job title"),
        "description": _opt(form, "description", 10000, e, "Description"),
        "job_url": _url(form, "job_url", e, "Job posting URL"),
        "location": _opt(form, "location", 120, e, "Location"),
        "employment_type": _choice(form, "employment_type", EMPLOYMENT_TYPES, "Internship", e, "Employment type"),
        "source": _opt(form, "source", 80, e, "Source"),
        "applied_on": _date(form, "applied_on", e, "Application date"),
        "deadline": _date(form, "deadline", e, "Deadline"),
        "comp_min": _int(form, "comp_min", e, "Minimum compensation"),
        "comp_max": _int(form, "comp_max", e, "Maximum compensation"),
        "comp_currency": _choice(form, "comp_currency", CURRENCIES, "INR", e, "Currency"),
        "comp_period": _choice(form, "comp_period", COMP_PERIODS, "year", e, "Pay period"),
        "status": _choice(form, "status", STATUSES, "Saved", e, "Status"),
        "notes": _opt(form, "notes", 10000, e, "Notes"),
    }
    if data["comp_min"] is not None and data["comp_max"] is not None and data["comp_max"] < data["comp_min"]:
        e["comp_max"] = "Maximum compensation must be greater than or equal to the minimum."
    if data["status"] != "Saved" and data["applied_on"] is None:
        # Anything past "Saved" has been submitted; default the date rather than reject.
        data["applied_on"] = None  # caller fills with user's local today
    return data, e


def parse_interview(form, tz: ZoneInfo) -> tuple[dict, dict]:
    e: dict[str, str] = {}
    raw = _s(form, "scheduled_at", 16)
    scheduled = None
    if not raw:
        e["scheduled_at"] = "Interview date and time is required."
    else:
        try:
            naive = dt.datetime.fromisoformat(raw)
            if naive.tzinfo is not None:
                raise ValueError
            scheduled = naive.replace(tzinfo=tz).astimezone(dt.timezone.utc)
            if not (2000 <= naive.year <= 2100):
                e["scheduled_at"] = "Interview date is out of range."
        except ValueError:
            e["scheduled_at"] = "Enter a valid date and time."
    data = {
        "scheduled_at": scheduled,
        "round_name": _req(form, "round_name", 80, e, "Round"),
        "interview_type": _choice(form, "interview_type", INTERVIEW_TYPES, "Technical", e, "Interview type"),
        "mode": _choice(form, "mode", INTERVIEW_MODES, "Online", e, "Mode"),
        "meeting_link": _url(form, "meeting_link", e, "Meeting link"),
        "prep_notes": _opt(form, "prep_notes", 10000, e, "Preparation notes"),
        "result": _choice(form, "result", INTERVIEW_RESULTS, "Pending", e, "Result"),
    }
    return data, e
