import io
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import Agent, AgentEvent, AgentStatus, AuditLog
from app.models.base import utcnow
from app.services.agents import create_enrollment
from app.services.offices import create_office
from tests.conftest import PW, login
from tests.factories import make_docx


# ---- health ---------------------------------------------------------------------------------------------------------

def test_healthz_is_public_and_minimal(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json == {"status": "ok"}


def test_healthz_reports_503_without_details(client, monkeypatch):
    import app.health as h

    def boom():
        raise RuntimeError("secret connection string")
    monkeypatch.setattr(h, "_check_db", boom)
    r = client.get("/healthz")
    assert r.status_code == 503 and r.json == {"status": "error"} and b"secret" not in r.data


# ---- CSP / vendored assets -------------------------------------------------------------------------------------------

def test_csp_has_no_third_party_hosts_and_bootstrap_is_served_locally(client):
    r = client.get("/login")
    csp = r.headers["Content-Security-Policy"]
    assert "cdn.jsdelivr" not in csp and "script-src 'self';" in csp and "style-src 'self';" in csp
    assert b"cdn.jsdelivr.net" not in r.data
    for f in ("bootstrap.min.css", "bootstrap.bundle.min.js"):
        assert client.get(f"/static/vendor/bootstrap/{f}").status_code == 200


def test_no_inline_style_attributes_in_templates(app):
    # style="..." attributes are blocked by the CSP (style-src 'self'); use classes instead
    bad = [p.name for p in (Path(app.root_path) / "templates").rglob("*.html") if ' style="' in p.read_text(encoding="utf-8")]
    assert bad == []


# ---- password change -------------------------------------------------------------------------------------------------

def test_change_password_requires_login(client):
    assert client.get("/account/password").status_code == 302
    assert client.post("/account/password", data={}).status_code == 302


def test_change_password_flow_and_audit_without_secrets(client, acme):
    _, _, worker = acme
    login(client, "worker@acme-corp.com")
    assert b"/account/password" in client.get("/").data
    new = "another-long-password"
    r = client.post("/account/password", data={"current": PW, "password": new, "confirm": new})
    assert r.status_code == 302
    db.session.refresh(worker)
    assert worker.check_password(new) and not worker.check_password(PW)
    blob = " ".join(str(a.details) for a in db.session.scalars(select(AuditLog)))
    assert "user.password_changed" in [a.action for a in db.session.scalars(select(AuditLog))]
    assert new not in blob and PW not in blob


@pytest.mark.parametrize("data,msg", [
    ({"current": "wrong-wrong-wrong", "password": "another-long-password", "confirm": "another-long-password"}, b"current password is not correct"),
    ({"current": PW, "password": "another-long-password", "confirm": "different-long-password"}, b"Passwords do not match"),
    ({"current": PW, "password": "short", "confirm": "short"}, b"at least 10"),
    ({"current": PW, "password": PW, "confirm": PW}, b"different from the current"),
])
def test_change_password_rejections_leave_password_unchanged(client, acme, data, msg):
    _, _, worker = acme
    login(client, "worker@acme-corp.com")
    r = client.post("/account/password", data=data)
    assert r.status_code == 200 and msg in r.data
    db.session.refresh(worker)
    assert worker.check_password(PW)


# ---- expired enrolment frees the agent slot ----------------------------------------------------------------------------

def test_expired_unused_enrolment_no_longer_blocks_a_new_one(acme):
    company, admin, _ = acme
    office = create_office(company, name="HQ", location=None, actor=admin)
    old, _ = create_enrollment(office, name="Old", actor=admin)
    db.session.commit()
    old.enrollment_expires_at = utcnow() - timedelta(days=1)
    db.session.commit()
    new, token = create_enrollment(office, name="New", actor=admin)
    db.session.commit()
    db.session.refresh(old)
    assert old.status == AgentStatus.REVOKED and old.enrollment_token_hash is None and new.status == AgentStatus.PENDING
    assert "agent.enrollment_expired" in [a.action for a in db.session.scalars(select(AuditLog))]
    assert db.session.scalar(select(AgentEvent).where(AgentEvent.event_type == "ENROLLMENT_EXPIRED")) is not None


def test_unexpired_enrolment_still_blocks_a_second_one(acme):
    from app.services.errors import ServiceError
    company, admin, _ = acme
    office = create_office(company, name="HQ", location=None, actor=admin)
    create_enrollment(office, name="One", actor=admin)
    db.session.commit()
    with pytest.raises(ServiceError):
        create_enrollment(office, name="Two", actor=admin)


# ---- misconfigured extension list is not a crash path --------------------------------------------------------------------

def test_extension_allowed_by_config_but_unsupported_is_rejected_not_500(app, client, acme):
    app.config["ALLOWED_EXTENSIONS"] = ("docx", "gif")
    login(client, "worker@acme-corp.com")
    r = client.post("/documents/upload", data={"file": (io.BytesIO(b"GIF89a"), "x.gif")}, content_type="multipart/form-data")
    assert r.status_code == 200 and b"not supported" in r.data
