import io
import re
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import AuditLog, Document, Printer, PrintJob, UserRole
from app.models import PrintJobStatus as S
from app.models.base import utcnow
from app.services import conversion_queue as cq
from app.services import print_jobs as pj
from app.services.agents import create_enrollment, register_agent, set_selected_printers
from app.services.offices import create_office
from app.services.users import create_user
from tests.conftest import PW, login
from tests.factories import make_docx

UUID0 = "00000000-0000-0000-0000-000000000000"


class _As:
    """One client per user; clears Flask-Login's cached user (flask.g leaks across clients inside one app context)."""
    def __init__(self, client):
        self._c = client

    def _clear(self):
        from flask import g
        g.pop("_login_user", None)

    def get(self, *a, **k):
        self._clear()
        return self._c.get(*a, **k)

    def post(self, *a, **k):
        self._clear()
        return self._c.post(*a, **k)


def client_for(app, email):
    c = app.test_client()
    _As(c)._clear()
    login(c, email)
    return _As(c)


def upload(c, name="a.docx", data=None):
    r = c.post("/documents/upload", data={"file": (io.BytesIO(data or make_docx()), name)}, content_type="multipart/form-data")
    assert r.status_code == 302
    return r.headers["Location"].rsplit("/", 1)[-1]


def set_up_office(company, admin, color=False):
    office = create_office(company, name="HQ", location=None, actor=admin)
    agent, token = create_enrollment(office, name="PC", actor=admin)
    db.session.commit()
    agent, _ = register_agent(token, "PC1", "0.1.0")
    set_selected_printers(agent, ["HP"], [{"windows_name": "HP", "status": "AVAILABLE", "supports_color": color}])
    db.session.commit()
    return agent


@pytest.fixture()
def rig(app, acme):
    company, admin, worker = acme
    agent = set_up_office(company, admin)
    agent.last_seen_at = utcnow() - timedelta(hours=1)  # registration counts as contact; start the test with the computer offline
    db.session.commit()
    w = client_for(app, "worker@acme-corp.com")
    pid = upload(w)
    cq.run_worker(once=True)
    return SimpleNamespace(app=app, company=company, admin=admin, worker=worker, agent=agent, w=w, pid=pid)


@pytest.fixture()
def second_worker(app, acme):
    c, _, _ = acme
    w = create_user(c, email="worker2@acme-corp.com", full_name="Second Worker", password=PW, role=UserRole.WORKER)
    db.session.commit()
    return w


def form(rig, **over):
    page = rig.w.get(f"/print/new/{rig.pid}").get_data(as_text=True)
    rid = re.search(r'name="request_id"[^>]*value="([0-9a-f]+)"', page) or re.search(r'value="([0-9a-f]{32})"', page)
    data = {"copies": "1", "paper_size": "A4", "color": "", "orientation": "", "request_id": rid.group(1)}
    data.update(over)
    return data


def make_job(rig, **kw):
    job, _ = pj.create_print_job(rig.worker, db.session.scalar(select(Document)), **kw)
    db.session.commit()
    return job


def make_unknown(rig):
    make_job(rig)
    job, token = pj.claim_next_job(rig.agent)  # the oldest queued job
    db.session.commit()
    for s in (S.DOWNLOADING, S.DOWNLOADED):
        pj.agent_report(rig.agent, pj.get_agent_job(rig.agent, job.public_id, token), s)
        db.session.commit()
    job.lease_expires_at = utcnow() - timedelta(seconds=5)
    db.session.commit()
    pj.recover_expired_leases()
    db.session.commit()
    assert job.status == S.UNKNOWN
    return job


def job_id(job):
    return str(job.public_id)


# ---- access ---------------------------------------------------------------------------------------------------------

def test_login_required_everywhere(client, acme):
    for url in ("/print/", "/print/history", "/print/attention", f"/print/new/{UUID0}", f"/print/{UUID0}", f"/print/{UUID0}/status"):
        assert client.get(url).status_code == 302
    for url in (f"/print/{UUID0}/cancel", f"/print/{UUID0}/resubmit", f"/print/{UUID0}/resolve"):
        assert client.post(url).status_code == 302


# ---- submission -----------------------------------------------------------------------------------------------------

def test_print_form_shows_status_and_hides_colour_for_mono_printer(rig):
    page = rig.w.get(f"/print/new/{rig.pid}").get_data(as_text=True)
    assert "a.docx" in page and "Send to office printer" in page and "HP" in page
    assert 'value="color"' not in page and "driver has the final say" in page
    assert "Not connected" in page and "will wait in the queue" in page  # agent never sent a heartbeat in this test
    printer = db.session.scalar(select(Printer))
    printer.supports_color = True
    db.session.commit()
    assert 'value="color"' in rig.w.get(f"/print/new/{rig.pid}").get_data(as_text=True)


def test_submit_creates_job_and_double_submit_is_one_job(rig):
    data = form(rig, copies="3", paper_size="Letter", orientation="landscape")
    r = rig.w.post(f"/print/new/{rig.pid}", data=data)
    assert r.status_code == 302
    again = rig.w.post(f"/print/new/{rig.pid}", data=data)  # same request id: double click / back-and-resubmit
    assert again.headers["Location"] == r.headers["Location"]
    jobs = db.session.scalars(select(PrintJob)).all()
    assert len(jobs) == 1 and (jobs[0].copies, jobs[0].paper_size, jobs[0].landscape, jobs[0].color) == (3, "Letter", True, None)
    page = rig.w.get(r.headers["Location"]).get_data(as_text=True)
    assert "Waiting for the office computer" in page and "3 copies" in page and "Cancel job" in page


def test_invalid_options_rejected_without_creating_a_job(rig):
    for over in ({"copies": "999"}, {"copies": "0"}, {"copies": "abc"}, {"paper_size": "B7"}, {"color": "color"}):
        r = rig.w.post(f"/print/new/{rig.pid}", data=form(rig, **over))
        assert r.status_code == 200
    assert db.session.scalar(select(PrintJob)) is None


def test_unconverted_document_cannot_be_printed(app, rig):
    pid = upload(rig.w, "b.docx", make_docx(["later"]))  # worker has not converted it yet
    r = rig.w.get(f"/print/new/{pid}")
    assert r.status_code == 302 and f"/documents/{pid}" in r.headers["Location"]


def test_no_agent_means_form_is_disabled_and_post_is_refused(app, acme):
    w = client_for(app, "worker@acme-corp.com")
    pid = upload(w)
    cq.run_worker(once=True)
    page = w.get(f"/print/new/{pid}").get_data(as_text=True)
    assert "No office computer is registered" in page and "disabled" in page
    rid = re.search(r'value="([0-9a-f]{32})"', page).group(1)
    r = w.post(f"/print/new/{pid}", data={"copies": "1", "paper_size": "A4", "color": "", "orientation": "", "request_id": rid})
    assert r.status_code == 200 and db.session.scalar(select(PrintJob)) is None


def test_viewing_policy_does_not_grant_printing_someone_elses_document(app, rig, second_worker):
    rig.company.workers_can_view_all_documents = True
    db.session.commit()
    w2 = client_for(app, "worker2@acme-corp.com")
    assert w2.get(f"/documents/{rig.pid}").status_code == 200
    assert w2.get(f"/print/new/{rig.pid}").status_code == 404
    assert b"/print/new/" not in w2.get(f"/documents/{rig.pid}").data  # no Print button either
    assert b"/print/new/" in rig.w.get(f"/documents/{rig.pid}").data


def test_admin_can_print_company_document(app, rig):
    a = client_for(app, "admin@acme-corp.com")
    assert a.get(f"/print/new/{rig.pid}").status_code == 200


# ---- lists, detail, isolation ---------------------------------------------------------------------------------------

def test_lists_filter_by_state_and_scope_by_user(app, rig, second_worker):
    queued = make_job(rig)
    failed = make_job(rig)
    pj.cancel_job(failed, rig.worker)
    db.session.commit()
    failed2 = make_job(rig)
    j, token = pj.claim_next_job(rig.agent)
    pj.agent_report(rig.agent, pj.get_agent_job(rig.agent, j.public_id, token), S.FAILED, error_code="X", error_message="boom")
    db.session.commit()
    assert j.id == queued.id  # oldest queued job was claimed (and failed)
    q = rig.w.get("/print/").get_data(as_text=True)
    assert "a.docx" in q and "Waiting" in q
    att = rig.w.get("/print/attention").get_data(as_text=True)
    assert "Failed" in att and "Cancelled" not in att
    hist = rig.w.get("/print/history").get_data(as_text=True)
    assert "Cancelled" in hist and "Failed" in hist
    w2 = client_for(app, "worker2@acme-corp.com")
    assert b"a.docx" not in w2.get("/print/history").data and b"No print jobs yet" in w2.get("/print/history").data
    admin = client_for(app, "admin@acme-corp.com").get("/print/history").get_data(as_text=True)
    assert "Requested by" in admin and "Acme Worker" in admin


def test_detail_status_json_and_isolation(app, rig, second_worker, other):
    job = make_job(rig)
    pid = job_id(job)
    assert rig.w.get(f"/print/{pid}/status").json["status"] == "QUEUED"
    w2 = client_for(app, "worker2@acme-corp.com")
    o = client_for(app, "admin@other-corp.com")
    for c in (w2, o):
        for suffix in ("", "/status"):
            assert c.get(f"/print/{pid}{suffix}").status_code == 404
        for action in ("cancel", "resubmit", "resolve"):
            assert c.post(f"/print/{pid}/{action}").status_code in (403, 404)
    assert client_for(app, "admin@acme-corp.com").get(f"/print/{pid}").status_code == 200
    assert rig.w.get(f"/print/{UUID0}").status_code == 404 and rig.w.get("/print/not-a-uuid").status_code == 404


def test_submitted_job_explains_it_is_not_proof_of_paper(app, rig):
    job = make_job(rig)
    j, token = pj.claim_next_job(rig.agent)
    for s in (S.DOWNLOADING, S.DOWNLOADED, S.SUBMITTED_TO_PRINTER):
        pj.agent_report(rig.agent, pj.get_agent_job(rig.agent, j.public_id, token), s)
    db.session.commit()
    page = rig.w.get(f"/print/{job_id(job)}").get_data(as_text=True)
    assert "Sent to printer" in page and "does not prove that paper has come out" in page and "Cancel job" not in page


# ---- actions --------------------------------------------------------------------------------------------------------

def test_cancel_and_submit_again(rig):
    job = make_job(rig)
    pid = job_id(job)
    assert rig.w.post(f"/print/{pid}/resubmit", follow_redirects=True).status_code == 200
    assert len(db.session.scalars(select(PrintJob)).all()) == 1  # queued jobs cannot be resubmitted
    r = rig.w.post(f"/print/{pid}/cancel", follow_redirects=True)
    assert b"Print job cancelled" in r.data
    db.session.refresh(job)
    assert job.status == S.CANCELLED
    r = rig.w.post(f"/print/{pid}/cancel", follow_redirects=True)
    assert b"can no longer be cancelled" in r.data
    r = rig.w.post(f"/print/{pid}/resubmit")
    assert r.status_code == 302 and len(db.session.scalars(select(PrintJob)).all()) == 2


def test_cannot_cancel_a_job_already_sent_to_the_printer(rig):
    job = make_job(rig)
    j, token = pj.claim_next_job(rig.agent)
    for s in (S.DOWNLOADING, S.DOWNLOADED):
        pj.agent_report(rig.agent, pj.get_agent_job(rig.agent, j.public_id, token), s)
    db.session.commit()
    assert b"already been sent" in rig.w.post(f"/print/{job_id(job)}/cancel", follow_redirects=True).data
    db.session.refresh(job)
    assert job.status == S.DOWNLOADED


def test_admin_settles_uncertain_job_and_worker_cannot(app, rig):
    job = make_unknown(rig)
    pid = job_id(job)
    wpage = rig.w.get(f"/print/{pid}").get_data(as_text=True)
    assert "NOT be printed again automatically" in wpage and "It printed" not in wpage
    assert rig.w.post(f"/print/{pid}/resolve", data={"outcome": "printed"}).status_code == 403
    a = client_for(app, "admin@acme-corp.com")
    assert "It did not print" in a.get(f"/print/{pid}").get_data(as_text=True)
    assert b"Job settled" in a.post(f"/print/{pid}/resolve", data={"outcome": "not_printed", "note": "tray empty"}, follow_redirects=True).data
    db.session.refresh(job)
    assert job.status == S.FAILED and job.error_code == "NOT_PRINTED_CONFIRMED"
    assert "print.resolved" in [x.action for x in db.session.scalars(select(AuditLog))]
    assert b"Submit again" in rig.w.get(f"/print/{pid}").data


def test_resolve_rejects_bad_outcome_and_non_uncertain_jobs(app, rig):
    a = client_for(app, "admin@acme-corp.com")
    job = make_job(rig)
    assert b"Only jobs with an uncertain outcome" in a.post(f"/print/{job_id(job)}/resolve", data={"outcome": "printed"}, follow_redirects=True).data
    unknown = make_unknown(rig)
    assert b"Choose" in a.post(f"/print/{job_id(unknown)}/resolve", data={"outcome": "maybe"}, follow_redirects=True).data
    db.session.refresh(unknown)
    assert unknown.status == S.UNKNOWN


def test_csrf_enforced_on_print_actions_when_enabled(app, rig):
    job = make_job(rig)
    app.config["WTF_CSRF_ENABLED"] = True
    r = rig.w.post(f"/print/{job_id(job)}/cancel")
    assert r.status_code in (302, 400)
    db.session.refresh(job)
    assert job.status == S.QUEUED
    assert rig.w.post(f"/print/new/{rig.pid}", data={"copies": "1", "paper_size": "A4"}).status_code in (200, 400)
    assert db.session.scalars(select(PrintJob)).all() == [job]


# ---- dashboard ------------------------------------------------------------------------------------------------------

def test_dashboard_shows_jobs_and_separate_connection_and_printer_status(app, rig):
    make_job(rig)
    page = rig.w.get("/").get_data(as_text=True)
    assert "Recent print jobs" in page and "1 waiting or in progress" in page and "a.docx" in page
    assert "Not connected" in page and "Windows reports: no problem" in page  # computer and printer shown separately
    assert "Printing is not available yet" not in page
    from app.services.agents import record_heartbeat
    record_heartbeat(rig.agent, "0.1.0", None)
    db.session.commit()
    assert "Connected" in rig.w.get("/").get_data(as_text=True)


def test_admin_dashboard_shows_print_totals(app, rig):
    job = make_unknown(rig)
    page = client_for(app, "admin@acme-corp.com").get("/").get_data(as_text=True)
    assert "1 print job in total" in page and "1 failed or uncertain" in page
