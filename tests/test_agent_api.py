import re

import pytest
from sqlalchemy import select

from app import create_app
from app.extensions import db
from app.models import Agent, AgentEvent, AgentStatus, AuditLog, Printer, PrinterAvailability
from app.models.base import utcnow
from app.services.agents import create_enrollment, is_online
from app.services.errors import ServiceError
from app.services.offices import create_office
from config import TestingConfig
from tests.conftest import login

API = "/api/agent/v1"
HP = {"windows_name": "HP LaserJet", "status": "AVAILABLE", "supports_color": False}
CANON = {"windows_name": "Canon", "status": "OFFLINE"}


@pytest.fixture()
def office(acme):
    company, admin, _ = acme
    o = create_office(company, name="HQ", location=None, actor=admin)
    db.session.commit()
    return o


def enroll(office, admin):
    agent, token = create_enrollment(office, name="Front PC", actor=admin)
    db.session.commit()
    return agent, token


def register(client, token, **extra):
    return client.post(f"{API}/register", json={"enrollment_token": token, "hostname": "OFFICE-PC", "agent_version": "0.1.0", **extra})


@pytest.fixture()
def active(client, acme, office):
    agent, token = enroll(office, acme[1])
    r = register(client, token)
    assert r.status_code == 201
    return agent, {"Authorization": "Bearer " + r.json["credential"]}, r.json["credential"]


def select_printers(client, h, enabled, installed=(HP, CANON)):
    return client.put(f"{API}/printers", headers=h, json={"enabled": enabled, "installed": list(installed)})


def test_register_issues_credential_stores_only_hashes_and_token_is_single_use(client, acme, office):
    agent, token = enroll(office, acme[1])
    assert agent.status == AgentStatus.PENDING and token not in (agent.enrollment_token_hash or "")
    r = register(client, token)
    cred = r.json["credential"]
    db.session.refresh(agent)
    assert r.status_code == 201 and agent.status == AgentStatus.ACTIVE and agent.hostname == "OFFICE-PC"
    assert agent.credential_hash != cred and cred not in str(agent.credential_hash) and agent.credential_prefix == cred[:12]
    assert agent.enrollment_token_hash is None
    again = register(client, token)
    assert again.status_code == 401 and again.json["error"]["code"] == "INVALID_ENROLLMENT"


def test_expired_and_garbage_tokens_rejected(client, acme, office):
    agent, token = enroll(office, acme[1])
    agent.enrollment_expires_at = utcnow().replace(year=2000)
    db.session.commit()
    assert register(client, token).status_code == 401
    assert register(client, "se_nonsense").status_code == 401
    assert client.post(f"{API}/register", json={}).status_code == 400
    assert client.post(f"{API}/register", data="x").status_code == 400


def test_one_agent_slot_per_office(acme, office):
    enroll(office, acme[1])
    with pytest.raises(ServiceError):
        create_enrollment(office, name="Second", actor=acme[1])


def test_heartbeat_requires_valid_credential(client, active):
    assert client.post(f"{API}/heartbeat", json={}).status_code == 401
    assert client.post(f"{API}/heartbeat", headers={"Authorization": "Bearer sa_wrong"}, json={}).status_code == 401
    assert client.post(f"{API}/heartbeat", headers={"Authorization": "Basic abc"}, json={}).status_code == 401


def test_heartbeat_updates_last_seen_and_connected_event(client, active):
    agent, h, _ = active
    agent.last_seen_at = utcnow().replace(year=2000)
    db.session.commit()
    assert not is_online(agent)
    r = client.post(f"{API}/heartbeat", headers=h, json={"agent_version": "0.2.0"})
    db.session.refresh(agent)
    assert r.status_code == 200 and is_online(agent) and agent.agent_version == "0.2.0"
    assert db.session.scalar(select(AgentEvent).where(AgentEvent.event_type == "CONNECTED")) is not None


def test_printer_selection_limit_swap_and_rejections(client, active):
    _, h, _ = active
    r = select_printers(client, h, ["HP LaserJet"])
    assert r.status_code == 200 and [p["windows_name"] for p in r.json["printers"]] == ["HP LaserJet"]
    r = select_printers(client, h, ["HP LaserJet", "Canon"])
    assert r.status_code == 409 and r.json["error"]["code"] == "SELECTION_REJECTED"
    assert len(db.session.scalars(select(Printer).where(Printer.is_active.is_(True))).all()) == 1  # unchanged
    assert select_printers(client, h, ["Canon"]).status_code == 200  # swap
    active_names = [p.windows_name for p in db.session.scalars(select(Printer).where(Printer.is_active.is_(True)))]
    assert active_names == ["Canon"]
    assert select_printers(client, h, ["Nope"]).status_code == 409
    assert client.put(f"{API}/printers", headers=h, json={"enabled": "x"}).status_code == 400
    assert client.put(f"{API}/printers", headers=h, json={"enabled": [], "installed": [{"windows_name": "", "status": "AVAILABLE"}]}).status_code == 400


def test_heartbeat_syncs_printer_availability_separately_from_agent(client, active):
    _, h, _ = active
    select_printers(client, h, ["HP LaserJet"])
    pr = db.session.scalar(select(Printer))
    db.session.refresh(pr)
    assert pr.availability == PrinterAvailability.AVAILABLE and pr.supports_color is False
    client.post(f"{API}/heartbeat", headers=h, json={"printers": [dict(HP, status="ERROR")]})
    db.session.refresh(pr)
    assert pr.availability == PrinterAvailability.ERROR
    client.post(f"{API}/heartbeat", headers=h, json={})  # no printers key: unchanged
    db.session.refresh(pr)
    assert pr.availability == PrinterAvailability.ERROR
    client.post(f"{API}/heartbeat", headers=h, json={"printers": []})  # removed from Windows
    db.session.refresh(pr)
    assert pr.availability == PrinterAvailability.OFFLINE
    assert db.session.scalar(select(AgentEvent).where(AgentEvent.event_type == "PRINTER_STATUS")) is not None
    assert client.post(f"{API}/heartbeat", headers=h, json={"printers": [{"windows_name": "X", "status": "BOGUS"}]}).status_code == 400


def test_rotate_invalidates_old_credential(client, active):
    _, h, _ = active
    new = client.post(f"{API}/credential/rotate", headers=h).json["credential"]
    assert client.post(f"{API}/heartbeat", headers=h, json={}).status_code == 401
    assert client.post(f"{API}/heartbeat", headers={"Authorization": "Bearer " + new}, json={}).status_code == 200


def test_unregister_revokes_agent(client, active):
    agent, h, _ = active
    assert client.post(f"{API}/unregister", headers=h).status_code == 200
    db.session.refresh(agent)
    assert agent.status == AgentStatus.REVOKED and agent.credential_hash is None
    assert client.post(f"{API}/heartbeat", headers=h, json={}).status_code == 401


def test_audit_rows_attribute_agent_and_never_hold_secrets(client, active):
    agent, h, cred = active
    select_printers(client, h, ["HP LaserJet"])
    rows = db.session.scalars(select(AuditLog)).all()
    assert any(r.action == "agent.registered" and r.actor_agent_id == agent.id for r in rows)
    assert any(r.action == "printer.registered" and r.actor_agent_id == agent.id for r in rows)
    blob = " ".join(str(r.details) for r in rows)
    assert cred not in blob and "se_" not in blob


def test_agent_api_not_blocked_by_csrf():
    app = create_app(TestingConfig, {"WTF_CSRF_ENABLED": True})
    with app.app_context():
        db.create_all()
        r = app.test_client().post(f"{API}/register", json={"enrollment_token": "se_x"})
        assert r.status_code == 401  # reached the handler (not a 400 CSRF rejection)


def test_admin_enrols_shows_token_once_and_revokes(client, acme, office):
    login(client, "admin@acme-corp.com")
    r = client.post("/admin/agent/enroll", data={"name": "Front desk PC"})
    token = re.search(rb"se_[A-Za-z0-9_-]{20,}", r.data).group().decode()
    agent = db.session.scalar(select(Agent))
    assert r.status_code == 200 and agent.status == AgentStatus.PENDING and agent.name == "Front desk PC"
    assert token not in str(agent.enrollment_token_hash)
    assert b"se_" not in client.get("/admin/office").data  # token is not shown again
    assert b"already has" in client.post("/admin/agent/enroll", data={}, follow_redirects=True).data
    client.post(f"/admin/agent/{agent.public_id}/revoke")
    db.session.refresh(agent)
    assert agent.status == AgentStatus.REVOKED
    assert register(client, token).status_code == 401


def test_agent_admin_actions_are_admin_only_and_company_scoped(client, acme, other, office):
    agent, _ = enroll(office, acme[1])
    login(client, "worker@acme-corp.com")
    assert client.post("/admin/agent/enroll", data={}).status_code == 403
    assert client.post(f"/admin/agent/{agent.public_id}/revoke").status_code == 403
    client.post("/logout")
    login(client, "admin@other-corp.com")
    assert client.post(f"/admin/agent/{agent.public_id}/revoke").status_code == 404
    db.session.refresh(agent)
    assert agent.status == AgentStatus.PENDING
