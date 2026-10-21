import os
import re
import tempfile

# Configure BEFORE the app is imported: tests run against a real PostgreSQL
# database (not SQLite) so behaviour matches production.
os.environ.setdefault("DATABASE_URL", "postgresql://jt:jt_local_pw@localhost:5432/jobtracker_test")
os.environ["APP_ENV"] = "test"
os.environ["COOKIE_SECURE"] = "false"
os.environ["STORAGE_BACKEND"] = "local"
os.environ["LOCAL_STORAGE_DIR"] = tempfile.mkdtemp(prefix="jt-test-uploads-")
os.environ.pop("SECRET_KEY", None)  # exercise the DB-stored signing key

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import models  # noqa: E402,F401
from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean():
    yield
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE " + ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
                          + " RESTART IDENTITY CASCADE"))


def csrf_of(resp) -> str:
    m = CSRF_RE.search(resp.text)
    assert m, "no csrf token on page"
    return m.group(1)


class Client:
    """Thin wrapper that fetches a fresh CSRF token from a page before each POST,
    exactly like a browser submitting a form."""

    def __init__(self):
        self.http = TestClient(app, follow_redirects=False)

    def get(self, url, **kw):
        return self.http.get(url, **kw)

    def post_form(self, url, data=None, page=None, files=None, **kw):
        token = csrf_of(self.http.get(page or url_page(url)))
        body = dict(data or {})
        body["csrf_token"] = token
        return self.http.post(url, data=body, files=files, **kw)

    def register(self, email="asha@example.com", password="Passw0rd!", name="Asha Rao"):
        r = self.post_form("/register", {"full_name": name, "email": email, "password": password,
                                         "confirm_password": password}, page="/register")
        assert r.status_code == 303, r.text
        return r

    def add_app(self, **fields):
        data = {"company": "Acme", "title": "SDE Intern", "status": "Applied", "employment_type": "Internship",
                "comp_currency": "INR", "comp_period": "month"}
        data.update(fields)
        r = self.post_form("/applications", data, page="/applications/new")
        assert r.status_code == 303, r.text
        return int(r.headers["location"].rsplit("/", 1)[1])


def url_page(url: str) -> str:
    # A page that renders a CSRF token for the logged-in or anonymous user.
    if url in ("/login", "/register"):
        return url
    return "/settings" if not url.startswith("/register") else "/register"


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def logged_in(client):
    client.register()
    return client
