from sqlalchemy import select

from app.db import SessionLocal
from app.models import User, UserSession

from .conftest import Client, csrf_of


def test_landing_and_protected_redirect(client):
    assert client.get("/").status_code == 200
    r = client.get("/dashboard")
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=%2Fdashboard")
    for path in ("/applications", "/applications/new", "/interviews", "/documents", "/settings"):
        assert client.get(path).status_code == 303, path


def test_register_hashes_password_and_logs_in(client):
    r = client.register()
    assert r.headers["location"] == "/dashboard"
    cookie = r.cookies.get("jt_session") or client.http.cookies.get("jt_session")
    assert cookie
    with SessionLocal() as db:
        u = db.scalar(select(User))
        assert u.email == "asha@example.com"
        assert u.password_hash.startswith("$argon2id$") and "Passw0rd!" not in u.password_hash
        # only a hash of the session token is stored
        s = db.scalar(select(UserSession))
        assert s.token_hash != cookie and len(s.token_hash) == 64
    assert client.get("/dashboard").status_code == 200


def test_register_validation(client):
    page = client.get("/register")
    bad = [
        ({"full_name": "", "email": "x@y.com", "password": "Passw0rd!", "confirm_password": "Passw0rd!"}, "Name is required"),
        ({"full_name": "A", "email": "not-an-email", "password": "Passw0rd!", "confirm_password": "Passw0rd!"}, "valid email"),
        ({"full_name": "A", "email": "a@b.com", "password": "short1", "confirm_password": "short1"}, "at least 8"),
        ({"full_name": "A", "email": "a@b.com", "password": "abcdefghi", "confirm_password": "abcdefghi"}, "letter and one number"),
        ({"full_name": "A", "email": "a@b.com", "password": "Passw0rd!", "confirm_password": "Other123"}, "do not match"),
    ]
    for data, msg in bad:
        r = client.http.post("/register", data={**data, "csrf_token": csrf_of(page)})
        assert r.status_code == 400 and msg in r.text, msg


def test_duplicate_email_case_insensitive(client):
    client.register(email="Asha@Example.com")
    other = Client()
    page = other.get("/register")
    r = other.http.post("/register", data={"full_name": "B", "email": "asha@example.COM", "password": "Passw0rd!",
                                           "confirm_password": "Passw0rd!", "csrf_token": csrf_of(page)})
    assert r.status_code == 400 and "already exists" in r.text


def test_login_logout_flow(client):
    client.register()
    client.post_form("/logout")
    assert client.get("/dashboard").status_code == 303
    r = client.post_form("/login", {"email": "ASHA@example.com", "password": "Passw0rd!", "next": "/applications"})
    assert r.status_code == 303 and r.headers["location"] == "/applications"
    assert client.get("/applications").status_code == 200


def test_logout_invalidates_session_server_side(client):
    client.register()
    token = client.http.cookies.get("jt_session")
    client.post_form("/logout")
    attacker = Client()
    attacker.http.cookies.set("jt_session", token)
    assert attacker.get("/dashboard").status_code == 303


def test_wrong_password_generic_message(client):
    client.register()
    client.post_form("/logout")
    r1 = client.post_form("/login", {"email": "asha@example.com", "password": "wrong-pass1"})
    r2 = client.post_form("/login", {"email": "nobody@example.com", "password": "wrong-pass1"})
    assert r1.status_code == r2.status_code == 401
    assert "Incorrect email or password" in r1.text and "Incorrect email or password" in r2.text


def test_login_rate_limit(client):
    client.register()
    client.post_form("/logout")
    for _ in range(5):
        assert client.post_form("/login", {"email": "asha@example.com", "password": "bad-guess1"}).status_code == 401
    r = client.post_form("/login", {"email": "asha@example.com", "password": "Passw0rd!"})
    assert r.status_code == 429 and "Too many failed attempts" in r.text


def test_open_redirect_blocked(client):
    client.register()
    client.post_form("/logout")
    for evil in ("https://evil.com", "//evil.com", "/\\evil.com"):
        r = client.post_form("/login", {"email": "asha@example.com", "password": "Passw0rd!", "next": evil})
        assert r.headers["location"] == "/dashboard", evil
        client.post_form("/logout")


def test_csrf_required(client):
    client.register()
    r = client.http.post("/applications", data={"company": "X", "title": "Y"})
    assert r.status_code == 403
    r = client.http.post("/applications", data={"company": "X", "title": "Y", "csrf_token": "forged"})
    assert r.status_code == 403
    # Cross-origin request with a valid token is still refused
    token = csrf_of(client.get("/applications/new"))
    r = client.http.post("/applications", data={"company": "X", "title": "Y", "csrf_token": token},
                         headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_change_password_signs_out_other_sessions(client):
    client.register()
    other_device = Client()
    other_device.post_form("/login", {"email": "asha@example.com", "password": "Passw0rd!"})
    assert other_device.get("/dashboard").status_code == 200
    r = client.post_form("/settings/password", {"current_password": "Passw0rd!", "new_password": "N3wPassword",
                                                "confirm_password": "N3wPassword"})
    assert r.status_code == 303
    assert client.get("/dashboard").status_code == 200
    assert other_device.get("/dashboard").status_code == 303


def test_security_headers_and_cookie_flags(client):
    r = client.get("/login")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    reg = client.register()
    set_cookie = reg.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=lax" in set_cookie


def test_health(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["database"] == "ok"
