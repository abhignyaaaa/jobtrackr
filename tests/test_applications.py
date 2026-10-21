import datetime as dt
import re

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Application, StatusChange

from .conftest import Client


def test_crud_and_history(logged_in):
    c = logged_in
    app_id = c.add_app(company="Zoho", title="Backend Intern", status="Saved", location="Chennai",
                       job_url="https://careers.zoho.com/x", comp_min="20000", comp_max="30000",
                       deadline="2030-01-15", notes="Referral from senior")
    page = c.get(f"/applications/{app_id}")
    assert page.status_code == 200 and "Backend Intern" in page.text and "₹20,000 – ₹30,000 / month" in page.text

    # edit
    r = c.post_form(f"/applications/{app_id}/edit", {"company": "Zoho Corp", "title": "Backend Intern",
                    "status": "Applied", "employment_type": "Internship", "comp_currency": "INR", "comp_period": "month"})
    assert r.status_code == 303
    with SessionLocal() as db:
        a = db.get(Application, app_id)
        assert a.company == "Zoho Corp" and a.status == "Applied"
        assert a.applied_on is not None  # auto-filled when moving past Saved
        hist = db.scalars(select(StatusChange).where(StatusChange.application_id == app_id)
                          .order_by(StatusChange.id)).all()
        assert [(h.from_status, h.to_status) for h in hist] == [(None, "Saved"), ("Saved", "Applied")]

    # quick status change with note
    c.post_form(f"/applications/{app_id}/status", {"status": "Assessment", "note": "OA link received"})
    assert "OA link received" in c.get(f"/applications/{app_id}").text

    # delete
    r = c.post_form(f"/applications/{app_id}/delete")
    assert r.status_code == 303
    assert c.get(f"/applications/{app_id}").status_code == 404
    with SessionLocal() as db:
        assert db.scalar(select(StatusChange).where(StatusChange.application_id == app_id)) is None


def test_validation_errors(logged_in):
    r = logged_in.post_form("/applications", {"company": "", "title": "x" * 200, "job_url": "javascript:alert(1)",
                                              "comp_min": "50", "comp_max": "10", "status": "Hacked",
                                              "deadline": "2024-13-45"}, page="/applications/new")
    assert r.status_code == 400
    for msg in ("Company is required", "at most 160", "valid http(s) link", "greater than or equal",
                "valid status", "valid date"):
        assert msg in r.text, msg


def test_xss_is_escaped(logged_in):
    app_id = logged_in.add_app(company="<script>alert(1)</script>", notes="<img src=x onerror=alert(1)>")
    html = logged_in.get(f"/applications/{app_id}").text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert "<img src=x" not in html


def test_users_cannot_access_each_others_data(logged_in):
    victim_app = logged_in.add_app(company="SecretCo", title="Secret Role")
    attacker = Client()
    attacker.register(email="eve@example.com")
    assert "SecretCo" not in attacker.get("/applications").text
    assert attacker.get(f"/applications/{victim_app}").status_code == 404
    assert attacker.get(f"/applications/{victim_app}/edit").status_code == 404
    for url, data in [(f"/applications/{victim_app}/edit", {"company": "pwned", "title": "x"}),
                      (f"/applications/{victim_app}/status", {"status": "Rejected"}),
                      (f"/applications/{victim_app}/interviews", {"scheduled_at": "2030-01-01T10:00", "round_name": "x"}),
                      (f"/applications/{victim_app}/delete", {})]:
        assert attacker.post_form(url, data).status_code == 404, url
    assert "SecretCo" not in attacker.get("/applications/export.csv").text
    with SessionLocal() as db:
        a = db.get(Application, victim_app)
        assert a.company == "SecretCo" and a.status == "Applied"
    # attacker's dashboard counts only their own data
    assert 'data-testid="stat-total">0<' in attacker.get("/dashboard").text


def test_search_filter_sort_paginate(logged_in):
    c = logged_in
    c.add_app(company="Google", title="SWE Intern", status="Applied", applied_on="2026-09-01", employment_type="Internship")
    c.add_app(company="Microsoft", title="Data Scientist", status="Interview", applied_on="2026-09-10", employment_type="Full-time")
    c.add_app(company="Zoho", title="ML Engineer 100%_match", status="Rejected", applied_on="2026-08-05", location="Chennai")
    for i in range(10):
        c.add_app(company=f"Filler{i:02d}", title="Role", status="Saved")

    def companies(url):
        return re.findall(r'<strong>([^<]+)</strong></a>', c.get(url).text)

    assert companies("/applications?q=data") == ["Microsoft"]
    assert companies("/applications?q=chennai") == ["Zoho"]
    assert companies("/applications?q=100%25_") == ["Zoho"]  # LIKE wildcards are escaped
    assert companies("/applications?status=Interview") == ["Microsoft"]
    assert companies("/applications?type=Full-time") == ["Microsoft"]
    assert companies("/applications?company=google") == ["Google"]
    assert set(companies("/applications?from=2026-09-01&to=2026-09-30")) == {"Google", "Microsoft"}
    asc = companies("/applications?sort=company&dir=asc")
    assert asc[0] == "Filler00" and len(asc) == 10
    page2 = companies("/applications?sort=company&dir=asc&page=2")
    assert page2 == ["Google", "Microsoft", "Zoho"]
    assert "Page 2 of 2" in c.get("/applications?sort=company&dir=asc&page=2").text
    # bogus parameters are ignored, not errors
    assert c.get("/applications?sort=drop_table&dir=sideways&page=abc&from=nope").status_code == 200


def test_csv_export_neutralises_formulas(logged_in):
    logged_in.add_app(company="=HYPERLINK(\"http://evil\")", title="Role")
    body = logged_in.get("/applications/export.csv").text
    assert "'=HYPERLINK" in body


def test_interview_flow(logged_in):
    c = logged_in
    app_id = c.add_app(company="Freshworks", status="Applied")
    future = (dt.datetime.now() + dt.timedelta(days=1)).strftime("%Y-%m-%dT10:30")
    r = c.post_form(f"/applications/{app_id}/interviews", {"scheduled_at": future, "round_name": "Technical 1",
                    "interview_type": "Technical", "mode": "Online", "meeting_link": "https://meet.google.com/abc",
                    "prep_notes": "Revise DP", "result": "Pending"})
    assert r.status_code == 303
    with SessionLocal() as db:
        a = db.get(Application, app_id)
        assert a.status == "Interview"  # auto-advanced
        iv = a.interviews[0]
        # 10:30 India time is stored as 05:00 UTC
        assert iv.scheduled_at.astimezone(dt.timezone.utc).strftime("%H:%M") == "05:00"
        iv_id = iv.id
    assert "Technical 1" in c.get("/interviews").text
    dash = c.get("/dashboard").text
    assert "Technical 1" in dash and 'data-testid="stat-interviews">1<' in dash
    # record result
    c.post_form(f"/interviews/{iv_id}/edit", {"scheduled_at": future, "round_name": "Technical 1",
                "interview_type": "Technical", "mode": "Online", "result": "Passed"})
    assert 'data-testid="stat-interviews">0<' in c.get("/dashboard").text
    # another user cannot touch it
    eve = Client(); eve.register(email="eve@example.com")
    assert eve.get(f"/interviews/{iv_id}/edit").status_code == 404
    assert eve.post_form(f"/interviews/{iv_id}/delete").status_code == 404
    assert c.post_form(f"/interviews/{iv_id}/delete").status_code == 303


def test_nonexistent_and_malformed_ids(logged_in):
    assert logged_in.get("/applications/999999").status_code == 404
    assert logged_in.get("/applications/abc").status_code == 404
