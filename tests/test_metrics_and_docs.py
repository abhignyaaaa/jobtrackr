import datetime as dt
import os
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Application, Document, User
from app.services.metrics import dashboard_metrics

from .conftest import Client


def _metrics(email="asha@example.com"):
    with SessionLocal() as db:
        uid = db.scalar(select(User.id).where(User.email == email))
        today = dt.datetime.now(ZoneInfo("Asia/Kolkata")).date()
        return dashboard_metrics(db, uid, today, dt.datetime.now(dt.timezone.utc))


def test_dashboard_metrics_come_from_database(logged_in):
    c = logged_in
    today = dt.datetime.now(ZoneInfo("Asia/Kolkata")).date()
    long_ago = (today.replace(day=1) - dt.timedelta(days=40)).isoformat()

    m = _metrics()
    assert m["total"] == 0 and m["interview_rate"] is None  # no denominator -> no fake percentage

    c.add_app(company="A", status="Saved")                                   # not submitted
    c.add_app(company="B", status="Applied", applied_on=today.isoformat())   # awaiting
    c.add_app(company="C", status="Applied", applied_on=long_ago)            # awaiting, older
    d = c.add_app(company="D", status="Applied", applied_on=today.isoformat())
    e = c.add_app(company="E", status="Applied", applied_on=today.isoformat())
    c.post_form(f"/applications/{d}/status", {"status": "Interview"})
    c.post_form(f"/applications/{d}/status", {"status": "Offer Received"})
    c.post_form(f"/applications/{e}/status", {"status": "Interview"})
    c.post_form(f"/applications/{e}/status", {"status": "Rejected"})

    m = _metrics()
    assert m["total"] == 5
    assert m["submitted"] == 4                 # B, C, D, E
    assert m["applied_this_month"] == 3        # B, D, E
    assert m["awaiting"] == 2                  # B, C
    assert m["offers"] == 1 and m["rejected"] == 1
    assert m["reached_interview"] == 2         # D and E both reached Interview (history-based)
    assert m["interview_rate"] == 50.0 and m["offer_rate"] == 25.0
    assert m["by_status"]["Saved"] == 1 and m["by_status"]["Offer Received"] == 1
    assert sum(m["activity"]["counts"]) >= 3

    html = c.get("/dashboard").text
    assert 'data-testid="stat-total">5<' in html
    assert 'data-testid="interview-rate">50.0%<' in html
    assert "2 of 4 submitted" in html

    # deleting data changes the numbers
    c.post_form(f"/applications/{d}/delete")
    assert _metrics()["total"] == 4 and _metrics()["offers"] == 0


def test_deadline_reminders(logged_in):
    c = logged_in
    today = dt.datetime.now(ZoneInfo("Asia/Kolkata")).date()
    c.add_app(company="Soon", status="Saved", deadline=(today + dt.timedelta(days=1)).isoformat())
    c.add_app(company="Later", status="Saved", deadline=(today + dt.timedelta(days=60)).isoformat())
    c.add_app(company="Missed", status="Saved", deadline=(today - dt.timedelta(days=3)).isoformat())
    c.add_app(company="Closed", status="Rejected", deadline=(today + dt.timedelta(days=1)).isoformat())
    html = c.get("/dashboard").text
    assert "Deadline tomorrow" in html and "Soon" in html
    assert "Later" not in html and "Closed" not in html
    assert "Deadline passed" in html and "Missed" in html
    assert 'class="pill"' in html  # nav badge for items due within 48h


PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


def test_document_upload_download_isolation(logged_in):
    c = logged_in
    app_id = c.add_app(company="Infosys")
    r = c.post_form("/documents", {"label": "Resume v1", "application_id": str(app_id)}, page="/documents",
                    files={"file": ("my resume.pdf", PDF, "application/pdf")})
    assert r.status_code == 303
    with SessionLocal() as db:
        doc = db.scalar(select(Document))
        assert doc.storage_key.startswith("users/") and doc.storage_key.endswith(".pdf")
        assert "resume" not in doc.storage_key  # key is opaque, not user-controlled
        assert doc.application_id == app_id
        doc_id = doc.id
        path = os.path.join(os.environ["LOCAL_STORAGE_DIR"], doc.storage_key)
    assert os.path.exists(path)

    r = c.get(f"/documents/{doc_id}/download")
    assert r.status_code == 200 and r.content == PDF
    assert "attachment" in r.headers["content-disposition"]

    eve = Client(); eve.register(email="eve@example.com")
    assert eve.get(f"/documents/{doc_id}/download").status_code == 404
    assert eve.post_form(f"/documents/{doc_id}/delete", page="/documents").status_code == 404
    assert "Resume v1" not in eve.get("/documents").text

    assert c.post_form(f"/documents/{doc_id}/delete", page="/documents").status_code == 303
    assert not os.path.exists(path)


def test_upload_validation(logged_in):
    c = logged_in
    cases = [
        (("evil.exe", b"MZ\x90\x00", "application/octet-stream"), "Only PDF, DOC and DOCX"),
        (("fake.pdf", b"<html><script>alert(1)</script>", "application/pdf"), "do not match"),
        (("empty.pdf", b"", "application/pdf"), "empty"),
        (("big.pdf", b"%PDF-" + b"0" * (5 * 1024 * 1024 + 10), "application/pdf"), "larger than 5 MB"),
    ]
    for f, msg in cases:
        r = c.post_form("/documents", {}, page="/documents", files={"file": f})
        assert r.status_code == 303
        assert msg in c.get("/documents").text, msg
    with SessionLocal() as db:
        assert db.scalar(select(Document)) is None


def test_upload_cannot_link_foreign_application(logged_in):
    eve = Client(); eve.register(email="eve@example.com")
    with SessionLocal() as db:
        pass
    victim_app = logged_in.add_app(company="Victim")
    eve.post_form("/documents", {"application_id": str(victim_app)}, page="/documents",
                  files={"file": ("cv.pdf", PDF, "application/pdf")})
    with SessionLocal() as db:
        doc = db.scalar(select(Document))
        assert doc is not None and doc.application_id is None
