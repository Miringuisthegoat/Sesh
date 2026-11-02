"""Sesh Agent core against the REAL Flask backend (agent API from Phase 4a), via the Flask test client instead of HTTP."""
import pytest
from sqlalchemy import select

pytest.importorskip("requests")

from app.extensions import db  # noqa: E402
from app.models import Agent, AgentStatus, Printer, PrinterAvailability  # noqa: E402
from app.services.agents import create_enrollment, is_online  # noqa: E402
from app.services.offices import create_office  # noqa: E402
from office_agent.agent_service import AgentService, State  # noqa: E402
from office_agent.cloud_client import CloudClient, Rejected  # noqa: E402
from office_agent.local_database import LocalDatabase  # noqa: E402
from office_agent.printer_manager import FakePrinterBackend, InstalledPrinter  # noqa: E402
from office_agent.secrets_store import CredentialStore, InsecureDevProtector  # noqa: E402

HP = InstalledPrinter("HP LaserJet", "AVAILABLE", False)
CANON = InstalledPrinter("Canon", "OFFLINE", True)


class _Resp:
    def __init__(self, r):
        self.status_code, self._r, self.headers = r.status_code, r, r.headers

    def json(self):
        data = self._r.get_json(silent=True)
        if data is None:
            raise ValueError("no json")
        return data


class FlaskTransport:
    def __init__(self, client):
        self.client = client

    def request(self, method, url, json=None, headers=None, **kw):
        from urllib.parse import urlsplit
        return _Resp(self.client.open(urlsplit(url).path, method=method, json=json, headers=headers))


@pytest.fixture()
def rig(app, client, acme, tmp_path):
    company, admin, _ = acme
    office = create_office(company, name="HQ", location=None, actor=admin)
    agent, token = create_enrollment(office, name="Front PC", actor=admin)
    db.session.commit()
    local = LocalDatabase(tmp_path / "agent.db")
    backend = FakePrinterBackend([HP, CANON])
    svc = AgentService(local, CredentialStore(local, InsecureDevProtector()), backend,
                       lambda url, cred: CloudClient(url, cred, session=FlaskTransport(client)), hostname="OFFICE-PC")
    return svc, agent, token, backend, local


def test_full_flow_register_select_heartbeat(rig):
    svc, agent, token, backend, _ = rig
    svc.register("http://localhost", token)
    db.session.refresh(agent)
    assert agent.status == AgentStatus.ACTIVE and agent.hostname == "OFFICE-PC"
    assert svc.set_enabled(["HP LaserJet"]) == ["HP LaserJet"]
    svc.tick()
    assert svc.state == State.CONNECTED and is_online(agent)
    pr = db.session.scalar(select(Printer))
    db.session.refresh(pr)
    assert pr.windows_name == "HP LaserJet" and pr.availability == PrinterAvailability.AVAILABLE
    backend.printers = [InstalledPrinter("HP LaserJet", "ERROR", False), CANON]  # e.g. paper jam
    svc.tick()
    db.session.refresh(pr)
    assert pr.availability == PrinterAvailability.ERROR and svc.state == State.CONNECTED  # agent up, printer not
    backend.printers = [CANON]  # printer removed from Windows
    svc.tick()
    db.session.refresh(pr)
    assert pr.availability == PrinterAvailability.OFFLINE


def test_second_printer_rejected_by_server_limit(rig):
    svc, _, token, _, local = rig
    svc.register("http://localhost", token)
    svc.set_enabled(["HP LaserJet"])
    with pytest.raises(Rejected) as e:
        svc.set_enabled(["HP LaserJet", "Canon"])
    assert "already has" in e.value.message and local.selected_printers() == ["HP LaserJet"]


def test_admin_revocation_is_noticed_on_next_heartbeat(rig):
    from app.services.agents import revoke_agent
    svc, agent, token, _, local = rig
    svc.register("http://localhost", token)
    svc.tick()
    revoke_agent(agent, None)
    db.session.commit()
    svc.tick()
    assert svc.state == State.REVOKED


def test_used_token_cannot_be_reused(rig):
    svc, _, token, _, _ = rig
    svc.register("http://localhost", token)
    svc.unregister()
    with pytest.raises(Rejected):
        svc.register("http://localhost", token)
