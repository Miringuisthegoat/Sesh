"""Whole chain through the real code paths: browser upload -> background conversion -> print form -> job queue ->
agent loop (fake printer program) -> job page. Only Windows and the physical printer are simulated."""
import re

import pytest
from sqlalchemy import select

pytest.importorskip("requests")

from app.extensions import db  # noqa: E402
from app.models import AuditLog, PrintJob  # noqa: E402
from app.models import PrintJobStatus as S  # noqa: E402
from app.services import conversion_queue as cq  # noqa: E402
from app.services.agents import create_enrollment  # noqa: E402
from app.services.offices import create_office  # noqa: E402
from office_agent.agent_service import AgentService, State  # noqa: E402
from office_agent.cloud_client import CloudClient  # noqa: E402
from office_agent.job_journal import JobJournal  # noqa: E402
from office_agent.job_processor import JobProcessor  # noqa: E402
from office_agent.local_database import LocalDatabase  # noqa: E402
from office_agent.print_submitter import FakeSubmitter  # noqa: E402
from office_agent.printer_manager import FakePrinterBackend, InstalledPrinter  # noqa: E402
from office_agent.secrets_store import CredentialStore, InsecureDevProtector  # noqa: E402
from tests.conftest import login  # noqa: E402
from tests.test_agent_jobs import FlaskTransport  # noqa: E402
from tests.test_documents_routes import up  # noqa: E402


def test_upload_convert_print_and_status_roundtrip(app, client, acme, tmp_path):
    company, admin, _ = acme
    office = create_office(company, name="HQ", location=None, actor=admin)
    _, token = create_enrollment(office, name="PC", actor=admin)
    db.session.commit()

    agent_http = app.test_client()  # the office computer: its own client, bearer credential only
    local = LocalDatabase(tmp_path / "agent.db")
    svc = AgentService(local, CredentialStore(local, InsecureDevProtector()),
                       FakePrinterBackend([InstalledPrinter("HP LaserJet", "AVAILABLE", False)]),
                       lambda url, cred: CloudClient(url, cred, session=FlaskTransport(agent_http)), hostname="PC")
    svc.register("http://localhost", token)
    svc.set_enabled(["HP LaserJet"])
    svc.tick()
    assert svc.state == State.CONNECTED
    submitter = FakeSubmitter()
    proc = JobProcessor(svc, JobJournal(local, InsecureDevProtector()), submitter, work_dir=tmp_path / "jobs")

    # a worker, away from the office, uploads and converts
    login(client, "worker@acme-corp.com")
    pid = up(client, "report.docx")
    cq.run_worker(once=True)
    assert client.get(f"/documents/{pid}/status").json["status"] == "CONVERTED"

    # prints it from the web form
    page = client.get(f"/print/new/{pid}").get_data(as_text=True)
    rid = re.search(r'value="([0-9a-f]{32})"', page).group(1)
    r = client.post(f"/print/new/{pid}", data={"copies": "2", "paper_size": "A4", "color": "", "orientation": "", "request_id": rid})
    assert r.status_code == 302
    job_url = r.headers["Location"]
    assert "Waiting" in client.get(job_url).get_data(as_text=True)

    # the office agent picks it up
    assert proc.run_once() is True
    job = db.session.scalar(select(PrintJob))
    db.session.refresh(job)
    assert job.status == S.SUBMITTED_TO_PRINTER and job.finished_at is None
    (opts, head), = submitter.calls
    assert opts.printer == "HP LaserJet" and opts.copies == 2 and head == b"%PDF-"

    # the worker sees the truthful status
    page = client.get(job_url).get_data(as_text=True)
    assert "Sent to printer" in page and "does not prove that paper has come out" in page
    actions = [a.action for a in db.session.scalars(select(AuditLog))]
    for expected in ("document.upload", "document.converted", "print.created", "print.claimed", "print.pdf_downloaded", "print.submitted_to_printer"):
        assert expected in actions
