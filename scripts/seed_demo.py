"""Create a demo account and fill it with sample applications THROUGH THE APP'S
OWN HTTP ENDPOINTS (register, add application, change status, add interview).

Nothing is inserted behind the app's back, so every dashboard number shown in
a demo is computed from real database rows created exactly as a user would.

Usage:
    python scripts/seed_demo.py --base-url https://your-app.onrender.com \
        --email demo@example.com --password 'YourDemoPass1'
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys

import httpx

CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--name", default="Demo Student")
    a = ap.parse_args()

    c = httpx.Client(base_url=a.base_url.rstrip("/"), follow_redirects=False, timeout=90)

    def token(page: str) -> str:
        r = c.get(page)
        m = CSRF.search(r.text)
        if not m:
            sys.exit(f"no CSRF token on {page} (status {r.status_code})")
        return m.group(1)

    def post(url: str, data: dict, page: str = "/settings") -> httpx.Response:
        return c.post(url, data={**data, "csrf_token": token(page)})

    r = post("/register", {"full_name": a.name, "email": a.email, "password": a.password,
                           "confirm_password": a.password}, page="/register")
    if r.status_code != 303:
        r = post("/login", {"email": a.email, "password": a.password}, page="/login")
        if r.status_code != 303:
            sys.exit("could not register or log in")
        print("account exists; logged in and adding data")

    today = dt.date.today()
    d = lambda n: (today - dt.timedelta(days=n)).isoformat()  # noqa: E731
    f = lambda n: (today + dt.timedelta(days=n)).isoformat()  # noqa: E731

    apps = [
        dict(company="Zoho", title="Software Developer Intern", status="Applied", applied_on=d(3), location="Chennai",
             employment_type="Internship", comp_min="30000", comp_max="35000", comp_period="month", source="Campus portal"),
        dict(company="Freshworks", title="Backend Engineer Intern", status="Applied", applied_on=d(10), location="Chennai",
             employment_type="Internship + PPO", source="Campus portal", job_url="https://www.freshworks.com/company/careers/"),
        dict(company="Microsoft", title="Software Engineer", status="Applied", applied_on=d(20), location="Hyderabad",
             employment_type="Full-time", comp_min="2200000", comp_period="year", source="Careers site"),
        dict(company="Infosys", title="Systems Engineer", status="Applied", applied_on=d(45), location="Mysuru",
             employment_type="Full-time", source="Campus portal"),
        dict(company="Razorpay", title="AI/ML Intern", status="Saved", deadline=f(2), location="Bengaluru",
             employment_type="Internship", source="LinkedIn", notes="Tailor resume for LLM projects before applying."),
        dict(company="Swiggy", title="Data Science Intern", status="Saved", deadline=f(9), location="Bengaluru",
             employment_type="Internship", source="LinkedIn"),
        dict(company="Atlassian", title="Graduate Software Engineer", status="Applied", applied_on=d(30),
             location="Bengaluru", employment_type="Full-time", source="Referral"),
        dict(company="TCS", title="Digital Engineer", status="Applied", applied_on=d(60), location="Chennai",
             employment_type="Full-time", source="Campus portal"),
    ]
    ids = {}
    for item in apps:
        data = {"comp_currency": "INR", "comp_period": "year", **item}
        r = post("/applications", data, page="/applications/new")
        if r.status_code != 303:
            sys.exit(f"failed to add {item['company']}: {r.status_code}")
        ids[item["company"]] = r.headers["location"].rsplit("/", 1)[1]
        print("added", item["company"])

    moves = [
        ("Freshworks", "Assessment", "Online assessment link received"),
        ("Atlassian", "Assessment", "HackerRank OA"), ("Atlassian", "Interview", "Cleared OA"),
        ("Atlassian", "Offer Received", "Offer letter received"),
        ("TCS", "Interview", "Shortlisted"), ("TCS", "Rejected", "Not selected after HR round"),
        ("Infosys", "Rejected", "Regret email"),
    ]
    for company, status, note in moves:
        post(f"/applications/{ids[company]}/status", {"status": status, "note": note})

    when = lambda days, hhmm: (dt.datetime.combine(today + dt.timedelta(days=days), dt.time.fromisoformat(hhmm))  # noqa: E731
                               .strftime("%Y-%m-%dT%H:%M"))
    interviews = [
        ("Microsoft", dict(scheduled_at=when(1, "10:30"), round_name="Technical Round 1", interview_type="Technical",
                           mode="Online", meeting_link="https://teams.microsoft.com/", prep_notes="DSA: graphs, DP. Revise OS + DBMS.")),
        ("Freshworks", dict(scheduled_at=when(5, "14:00"), round_name="Technical Round 1", interview_type="Technical",
                            mode="Online", prep_notes="FastAPI project deep-dive, SQL joins.")),
        ("TCS", dict(scheduled_at=when(-12, "11:00"), round_name="HR Round", interview_type="HR", mode="On-site",
                     result="Failed")),
    ]
    for company, data in interviews:
        post(f"/applications/{ids[company]}/interviews", {"result": "Pending", **data})
    print("demo data ready for", a.email)


if __name__ == "__main__":
    main()
