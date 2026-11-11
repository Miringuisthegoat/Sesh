from sqlalchemy import select

from app.extensions import db
from app.models import AuditLog, Company, User
from app import create_app
from config import TestingConfig
from tests.conftest import PW, login


def test_signup_creates_company_and_admin_then_disappears(client):
    r = client.post("/signup", data={"company_name": "Acme", "full_name": "Ann", "email": "ann@acme-corp.com",
                                    "password": PW, "confirm": PW})
    assert r.status_code == 302
    assert db.session.scalar(select(Company).where(Company.name == "Acme")) is not None
    assert db.session.scalar(select(User).where(User.email == "ann@acme-corp.com")).is_admin
    assert client.get("/signup").status_code == 404
    assert client.get("/setup").status_code == 404


def test_login_redirects_to_setup_when_no_company(client):
    assert client.get("/login").headers["Location"].endswith("/signup")


def test_setup_remains_an_alias_for_first_run_signup(client):
    response = client.get("/setup")
    assert response.status_code == 200 and b"Create your Sesh account" in response.data


def test_password_is_hashed_not_plaintext(acme):
    _, admin, _ = acme
    assert admin.password_hash != PW and PW not in admin.password_hash
    assert admin.password_hash.startswith(("scrypt:", "pbkdf2:"))
    assert admin.check_password(PW) and not admin.check_password("wrong")


def test_login_success_and_dashboard(client, acme):
    assert login(client, "ADMIN@acme-corp.com").status_code == 302  # email case-insensitive
    assert client.get("/").status_code == 200


def test_login_failure_is_generic(client, acme):
    r1 = client.post("/login", data={"email": "admin@acme-corp.com", "password": "nope-nope-nope"})
    r2 = client.post("/login", data={"email": "ghost@acme-corp.com", "password": "nope-nope-nope"})
    assert r1.status_code == r2.status_code == 200
    assert b"Invalid email or password." in r1.data and b"Invalid email or password." in r2.data
    assert b"Welcome back" not in client.get("/").data


def test_inactive_user_cannot_login_and_is_cut_off_immediately(client, acme):
    _, _, worker = acme
    login(client, "worker@acme-corp.com")
    assert client.get("/").status_code == 200
    worker.is_active = False
    db.session.commit()
    assert b"Welcome back" not in client.get("/").data  # session no longer valid
    r = client.post("/login", data={"email": "worker@acme-corp.com", "password": PW})
    assert r.status_code == 200


def test_open_redirect_is_blocked(client, acme):
    r = client.post("/login?next=https://evil.example/x", data={"email": "admin@acme-corp.com", "password": PW})
    assert r.headers["Location"] == "/"
    r = client.post("/login?next=//evil.example", data={"email": "admin@acme-corp.com", "password": PW})
    assert r.headers["Location"] == "/"


def test_logout_requires_post(client, acme):
    login(client, "admin@acme-corp.com")
    assert client.get("/logout").status_code == 405
    assert client.post("/logout").status_code == 302
    assert b"Welcome back" not in client.get("/").data


def test_csrf_enforced_when_enabled():
    app = create_app(TestingConfig, {"WTF_CSRF_ENABLED": True})
    with app.app_context():
        db.create_all()
        r = app.test_client().post("/login", data={"email": "a@b.com", "password": "x"})
        assert r.status_code == 400


def test_session_cookie_flags(client, acme):
    r = login(client, "admin@acme-corp.com")
    cookie = [h for h in r.headers.getlist("Set-Cookie") if h.startswith("session=")][0]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_login_rate_limit():
    app = create_app(TestingConfig, {"RATELIMIT_ENABLED": True, "LOGIN_RATE_LIMIT": "3 per minute"})
    with app.app_context():
        db.create_all()
        c = app.test_client()
        codes = [c.post("/login", data={"email": "a@b.com", "password": "x"}).status_code for _ in range(5)]
        assert 429 in codes and codes[0] != 429


def test_security_headers(client):
    r = client.get("/login")
    assert r.headers["X-Frame-Options"] == "DENY" and "default-src 'self'" in r.headers["Content-Security-Policy"]


def test_production_requires_real_secret_key():
    import pytest
    with pytest.raises(RuntimeError):
        create_app(TestingConfig, {"ENVIRONMENT": "production", "SECRET_KEY": "dev-only-insecure-key-do-not-use-in-production"})
