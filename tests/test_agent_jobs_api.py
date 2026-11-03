import hashlib
import io
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import AuditLog, PrintJob
from app.models import PrintJobStatus as S
from app.services import conversion_queue as cq
from app.services import print_jobs as pj
from app.services.agents import create_enrollment
from app.services.documents import ingest_upload
from app.services.offices import create_office
from tests.factories import make_docx

API = "/api/agent/v1"
HP = {"windows_name": "HP", "status": "AVAILABLE", "supports_color": False}


class FakeUpload:
    def __init__(self, name, data):
        self.filename, self.mimetype, self.stream = name, "", io.BytesIO(data)


def make_agent(client, company, admin, name="HQ"):
    office = create_office(company, name=name, location=None, actor=admin)
    _, token = create_enrollment(office, name="PC", actor=admin)
    db.session.commit()
    cred = client.post(f"{API}/register", json={"enrollment_token": token, "hostname": "PC", "agent_version": "1"}).json["credential"]
    h = {"Authorization": "Bearer " + cred}
    assert client.put(f"{API}/printers", headers=h, json={"enabled": ["HP"], "installed": [HP]}).status_code == 200
    return h


@pytest.fixture()
def rig(app, client, acme):
    company, admin, worker = acme
    h = make_agent(client, company, admin)
    doc = ingest_upload(FakeUpload("a.docx", make_docx()), worker)
    cq.run_worker(once=True)
    db.session.refresh(doc)
    return SimpleNamespace(h=h, doc=doc, worker=worker, admin=admin, company=company)


def queue(rig, **kw):
    job, _ = pj.create_print_job(rig.worker, rig.doc, **kw)
    db.session.commit()
    return job


def claim(client, rig):
    return client.post(f"{API}/jobs/claim", headers=rig.h).json["job"]


def set_status(client, rig, job, token, status, **extra):
    return client.post(f"{API}/jobs/{job['id']}/status", headers=rig.h, json={"claim_token": token, "status": status, **extra})


def test_job_endpoints_require_agent_credential(client, rig):
    jid = "00000000-0000-0000-0000-000000000000"
    for method, url in (("post", "/jobs/claim"), ("get", f"/jobs/{jid}/pdf"), ("post", f"/jobs/{jid}/status"), ("post", f"/jobs/{jid}/lease")):
        assert getattr(client, method)(API + url).status_code == 401
        assert getattr(client, method)(API + url, headers={"Authorization": "Bearer sa_wrong"}).status_code == 401


def test_claim_returns_none_then_job_once(client, rig):
    assert client.post(f"{API}/jobs/claim", headers=rig.h).json == {"job": None}
    queued = queue(rig, copies=2, paper_size="Letter", landscape=True)
    job = claim(client, rig)
    assert job["id"] == str(queued.public_id) and job["claim_token"].startswith("sj_")
    assert (job["printer"], job["copies"], job["paper_size"], job["landscape"], job["color"]) == ("HP", 2, "Letter", True, None)
    assert len(job["sha256"]) == 64 and job["size_bytes"] > 0 and job["pages"] >= 1
    assert client.post(f"{API}/jobs/claim", headers=rig.h).json == {"job": None}
    db.session.refresh(queued)
    assert queued.status == S.CLAIMED and job["claim_token"] not in queued.claim_token_hash


def test_pdf_requires_the_claim_token_and_matches_checksum(client, rig):
    queue(rig)
    job = claim(client, rig)
    url = f"{API}/jobs/{job['id']}/pdf"
    for bad in ({}, {"X-Claim-Token": "sj_wrong"}):
        assert client.get(url, headers={**rig.h, **bad}).status_code == 404
    r = client.get(url, headers={**rig.h, "X-Claim-Token": job["claim_token"]})
    assert r.status_code == 200 and r.mimetype == "application/pdf" and r.data.startswith(b"%PDF-")
    assert hashlib.sha256(r.data).hexdigest() == job["sha256"] == r.headers["X-Content-SHA256"]
    assert int(r.headers["Content-Length"]) == len(r.data) == job["size_bytes"]
    assert "print.pdf_downloaded" in [a.action for a in db.session.scalars(select(AuditLog))]


def test_pdf_not_served_once_job_is_cancelled(client, rig):
    queued = queue(rig)
    job = claim(client, rig)
    pj.cancel_job(queued, rig.worker)
    db.session.commit()
    r = client.get(f"{API}/jobs/{job['id']}/pdf", headers={**rig.h, "X-Claim-Token": job["claim_token"]})
    assert r.status_code == 409 and r.json["error"]["code"] == "JOB_NOT_AVAILABLE" and r.json["error"]["job_status"] == "CANCELLED"


def test_status_flow_duplicates_and_validation(client, rig):
    queued = queue(rig)
    job = claim(client, rig)
    tok = job["claim_token"]
    assert set_status(client, rig, job, tok, "DOWNLOADING").json["job"]["status"] == "DOWNLOADING"
    assert set_status(client, rig, job, tok, "downloading").status_code == 200  # duplicate delivery, case-insensitive
    assert set_status(client, rig, job, tok, "DOWNLOADED").status_code == 200
    assert set_status(client, rig, job, tok, "SUBMITTED_TO_PRINTER").status_code == 200
    db.session.refresh(queued)
    assert queued.status == S.SUBMITTED_TO_PRINTER and queued.submitted_at is not None and queued.finished_at is None
    assert set_status(client, rig, job, tok, "BOGUS").status_code == 400
    assert client.post(f"{API}/jobs/{job['id']}/status", headers=rig.h, json={"status": "FAILED"}).status_code == 404  # no token
    assert client.post(f"{API}/jobs/{job['id']}/status", headers=rig.h, data="x").status_code == 400
    assert set_status(client, rig, job, tok, "FAILED", error_code=5).status_code == 400


def test_illegal_and_forbidden_transitions_are_409(client, rig):
    queue(rig)
    job = claim(client, rig)
    r = set_status(client, rig, job, job["claim_token"], "SUBMITTED_TO_PRINTER")
    assert r.status_code == 409 and r.json["error"]["code"] == "ILLEGAL_TRANSITION" and r.json["error"]["job_status"] == "CLAIMED"
    assert set_status(client, rig, job, job["claim_token"], "CANCELLED").status_code == 409


def test_cancelled_job_tells_the_agent_to_stop(client, rig):
    queued = queue(rig)
    job = claim(client, rig)
    pj.cancel_job(queued, rig.worker)
    db.session.commit()
    r = set_status(client, rig, job, job["claim_token"], "DOWNLOADING")
    assert r.status_code == 409 and r.json["error"]["code"] == "JOB_CANCELLED"


def test_release_hands_job_back_and_token_dies(client, rig):
    queued = queue(rig)
    job = claim(client, rig)
    assert set_status(client, rig, job, job["claim_token"], "QUEUED").status_code == 200
    db.session.refresh(queued)
    assert queued.status == S.QUEUED and queued.agent_id is None
    assert set_status(client, rig, job, job["claim_token"], "DOWNLOADING").status_code == 404
    again = claim(client, rig)
    assert again["id"] == job["id"] and again["claim_token"] != job["claim_token"]


def test_failed_status_records_error(client, rig):
    queued = queue(rig)
    job = claim(client, rig)
    r = set_status(client, rig, job, job["claim_token"], "FAILED", error_code="CHECKSUM_MISMATCH", error_message="bad pdf")
    db.session.refresh(queued)
    assert r.status_code == 200 and queued.status == S.FAILED and queued.error_code == "CHECKSUM_MISMATCH" and queued.finished_at


def test_lease_endpoint_renews_only_held_jobs(client, rig):
    queued = queue(rig)
    job = claim(client, rig)
    assert client.post(f"{API}/jobs/{job['id']}/lease", headers=rig.h, json={"claim_token": job["claim_token"]}).status_code == 200
    assert client.post(f"{API}/jobs/{job['id']}/lease", headers=rig.h, json={"claim_token": "sj_x"}).status_code == 404
    pj.cancel_job(queued, rig.worker)
    db.session.commit()
    r = client.post(f"{API}/jobs/{job['id']}/lease", headers=rig.h, json={"claim_token": job["claim_token"]})
    assert r.status_code == 409


def test_other_companys_agent_cannot_claim_or_read_jobs(client, rig, other):
    queued = queue(rig)
    o_company, o_admin, _ = other
    other_h = make_agent(client, o_company, o_admin, name="Other HQ")
    assert client.post(f"{API}/jobs/claim", headers=other_h).json == {"job": None}
    job = claim(client, rig)
    r = client.get(f"{API}/jobs/{job['id']}/pdf", headers={**other_h, "X-Claim-Token": job["claim_token"]})
    assert r.status_code == 404
    assert client.post(f"{API}/jobs/{job['id']}/status", headers=other_h, json={"claim_token": job["claim_token"], "status": "FAILED"}).status_code == 404
    db.session.refresh(queued)
    assert queued.status == S.CLAIMED
