import io
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update
from werkzeug.exceptions import NotFound

from app.extensions import db
from app.models import AuditLog, PrintJob, UserRole
from app.models import PrintJobStatus as S
from app.models.base import utcnow
from app.services import conversion_queue as cq
from app.services import print_jobs as pj
from app.services.agents import create_enrollment, register_agent, set_selected_printers
from app.services.documents import ingest_upload
from app.services.errors import ServiceError
from app.services.offices import create_office
from app.services.users import create_user
from tests.conftest import PW
from tests.factories import make_docx


class FakeUpload:
    def __init__(self, name, data):
        self.filename, self.mimetype, self.stream = name, "", io.BytesIO(data)


@pytest.fixture()
def rig(app, acme):
    company, admin, worker = acme
    office = create_office(company, name="HQ", location=None, actor=admin)
    agent, token = create_enrollment(office, name="PC", actor=admin)
    db.session.commit()
    agent, _ = register_agent(token, "PC1", "0.1.0")
    set_selected_printers(agent, ["HP"], [{"windows_name": "HP", "status": "AVAILABLE", "supports_color": False}])
    db.session.commit()
    doc = ingest_upload(FakeUpload("a.docx", make_docx()), worker)
    cq.run_worker(once=True)
    db.session.refresh(doc)
    return SimpleNamespace(company=company, admin=admin, worker=worker, agent=agent, doc=doc, office=office)


def new_job(rig, **kw):
    job, _ = pj.create_print_job(rig.worker, rig.doc, **kw)
    db.session.commit()
    return job


def claim(rig):
    res = pj.claim_next_job(rig.agent)
    db.session.commit()
    return res


def report(rig, job, token, to, **kw):
    j = pj.get_agent_job(rig.agent, job.public_id, token)
    out = pj.agent_report(rig.agent, j, to, **kw)
    db.session.commit()
    return out


def advance_to_downloaded(rig):
    job = new_job(rig)
    _, token = claim(rig)
    report(rig, job, token, S.DOWNLOADING)
    report(rig, job, token, S.DOWNLOADED)
    return job, token


def expire(job):
    job.lease_expires_at = utcnow() - timedelta(seconds=5)
    db.session.commit()


def actions():
    return [a.action for a in db.session.scalars(select(AuditLog).order_by(AuditLog.id))]


# ---- transition table ---------------------------------------------------------------------------------------------

def test_transition_table_is_complete_and_terminal_states_have_no_exit():
    assert set(pj.TRANSITIONS) == set(S)
    for st in pj.TERMINAL:
        assert pj.TRANSITIONS[st] == frozenset()
    assert not pj.can_transition(S.QUEUED, S.SUBMITTED_TO_PRINTER)
    assert not pj.can_transition(S.DOWNLOADED, S.QUEUED)  # never re-queue something that may have printed
    assert not pj.can_transition(S.SUBMITTED_TO_PRINTER, S.QUEUED)


# ---- creation -----------------------------------------------------------------------------------------------------

def test_create_job_defaults_and_audit(rig):
    job = new_job(rig)
    assert job.status == S.QUEUED and job.copies == 1 and job.paper_size == rig.company.default_paper_size
    assert job.office_id == rig.office.id and job.requested_by_id == rig.worker.id
    assert job.converted_file.document_id == rig.doc.id and job.claim_token_hash is None
    assert "print.created" in actions()


@pytest.mark.parametrize("kw", [dict(copies=0), dict(copies=999), dict(copies=True), dict(copies="2"),
                                dict(paper_size="B7"), dict(color=True), dict(landscape="yes")])
def test_invalid_options_rejected(rig, kw):
    with pytest.raises(ServiceError):
        pj.create_print_job(rig.worker, rig.doc, **kw)
    assert db.session.scalar(select(PrintJob)) is None


def test_valid_options_stored(rig):
    job = new_job(rig, copies=3, paper_size="Letter", color=False, landscape=True)
    assert (job.copies, job.paper_size, job.color, job.landscape) == (3, "Letter", False, True)


def test_unconverted_document_and_missing_office_rejected(app, acme):
    _, _, worker = acme
    doc = ingest_upload(FakeUpload("a.docx", make_docx()), worker)
    with pytest.raises(ServiceError, match="not been converted"):
        pj.create_print_job(worker, doc)
    cq.run_worker(once=True)
    db.session.refresh(doc)
    with pytest.raises(ServiceError, match="office is not set up"):
        pj.create_print_job(worker, doc)


def test_no_agent_or_printer_rejected(app, acme):
    company, admin, worker = acme
    doc = ingest_upload(FakeUpload("a.docx", make_docx()), worker)
    cq.run_worker(once=True)
    db.session.refresh(doc)
    office = create_office(company, name="HQ", location=None, actor=admin)
    db.session.commit()
    with pytest.raises(ServiceError, match="No office computer"):
        pj.create_print_job(worker, doc)
    agent, token = create_enrollment(office, name="PC", actor=admin)
    db.session.commit()
    agent, _ = register_agent(token, "PC1", "0.1.0")
    db.session.commit()
    with pytest.raises(ServiceError, match="No printer"):
        pj.create_print_job(worker, doc)


def test_idempotency_key_returns_same_job_and_rejects_other_document(rig):
    j1, created1 = pj.create_print_job(rig.worker, rig.doc, idempotency_key="abc")
    j2, created2 = pj.create_print_job(rig.worker, rig.doc, idempotency_key="abc")
    db.session.commit()
    assert created1 and not created2 and j1.id == j2.id
    assert len(db.session.scalars(select(PrintJob)).all()) == 1
    doc2 = ingest_upload(FakeUpload("b.docx", make_docx(["other"])), rig.worker)
    cq.run_worker(once=True)
    db.session.refresh(doc2)
    with pytest.raises(ServiceError, match="different document"):
        pj.create_print_job(rig.worker, doc2, idempotency_key="abc")


def test_pending_job_cap(rig, app):
    app.config["MAX_PENDING_JOBS_PER_USER"] = 2
    new_job(rig)
    new_job(rig)
    with pytest.raises(ServiceError, match="too many jobs"):
        pj.create_print_job(rig.worker, rig.doc)


def test_other_company_cannot_print_this_document(rig, other):
    _, other_admin, _ = other
    with pytest.raises(NotFound):
        pj.create_print_job(other_admin, rig.doc)


# ---- visibility ---------------------------------------------------------------------------------------------------

def test_visibility_is_scoped_to_owner_admin_and_company(rig, other):
    job = new_job(rig)
    w2 = create_user(rig.company, email="w2@acme-corp.com", full_name="Two", password=PW, role=UserRole.WORKER)
    db.session.commit()
    _, other_admin, _ = other
    assert pj.get_print_job_or_404(job.public_id, rig.worker).id == job.id
    assert pj.get_print_job_or_404(job.public_id, rig.admin).id == job.id
    for u in (w2, other_admin):
        with pytest.raises(NotFound):
            pj.get_print_job_or_404(job.public_id, u)
    assert db.session.scalars(pj.visible_print_jobs(w2)).all() == []


# ---- claiming and the happy path ----------------------------------------------------------------------------------

def test_claim_is_exclusive_fifo_and_token_is_hashed(rig):
    j1, j2 = new_job(rig), new_job(rig)
    c1, c2, c3 = claim(rig), claim(rig), claim(rig)
    assert c1[0].id == j1.id and c2[0].id == j2.id and c3 is None
    job, token = c1
    assert job.status == S.CLAIMED and job.agent_id == rig.agent.id and job.lease_expires_at is not None
    assert token not in (job.claim_token_hash or "") and len(job.claim_token_hash) == 64


def test_claim_ignores_jobs_for_other_printers(rig):
    job = new_job(rig)
    set_selected_printers(rig.agent, [], [{"windows_name": "HP", "status": "AVAILABLE"}])
    db.session.commit()
    assert claim(rig) is None and db.session.get(PrintJob, job.id).status == S.QUEUED


def test_full_happy_path_and_duplicate_reports_are_noops(rig):
    job = new_job(rig)
    _, token = claim(rig)
    report(rig, job, token, S.DOWNLOADING)
    report(rig, job, token, S.DOWNLOADING)  # duplicate delivery
    report(rig, job, token, S.DOWNLOADED)
    assert job.lease_expires_at is not None
    report(rig, job, token, S.SUBMITTED_TO_PRINTER)
    report(rig, job, token, S.SUBMITTED_TO_PRINTER)
    assert job.status == S.SUBMITTED_TO_PRINTER and job.submitted_at is not None
    assert job.lease_expires_at is None and job.finished_at is None  # submitted is NOT completed
    assert actions().count("print.submitted_to_printer") == 1


def test_wrong_token_other_job_or_released_job_is_refused(rig):
    job = new_job(rig)
    _, token = claim(rig)
    with pytest.raises(pj.JobAuthError):
        pj.get_agent_job(rig.agent, job.public_id, "sj_wrong")
    with pytest.raises(pj.JobAuthError):
        pj.get_agent_job(rig.agent, job.public_id, None)
    report(rig, job, token, S.QUEUED)  # agent hands it back
    assert job.status == S.QUEUED and job.agent_id is None
    with pytest.raises(pj.JobAuthError):
        pj.get_agent_job(rig.agent, job.public_id, token)


def test_agent_cannot_make_illegal_or_forbidden_moves(rig):
    job = new_job(rig)
    _, token = claim(rig)
    with pytest.raises(pj.JobTransitionError):
        report(rig, job, token, S.SUBMITTED_TO_PRINTER)  # skipping DOWNLOADING/DOWNLOADED
    with pytest.raises(pj.JobTransitionError):
        report(rig, job, token, S.CANCELLED)  # agents never cancel
    with pytest.raises(pj.JobTransitionError):
        report(rig, job, token, S.COMPLETED)
    assert job.status == S.CLAIMED


def test_failed_report_stores_safe_error_and_finishes(rig):
    job = new_job(rig)
    _, token = claim(rig)
    report(rig, job, token, S.FAILED, error_code="CHECKSUM_MISMATCH", error_message="x" * 900)
    assert job.status == S.FAILED and job.error_code == "CHECKSUM_MISMATCH" and len(job.error_message) == 500
    assert job.finished_at is not None


def test_lost_race_is_detected_by_conditional_update(rig):
    job = new_job(rig)
    _, token = claim(rig)
    stale = pj.get_agent_job(rig.agent, job.public_id, token)
    db.session.execute(update(PrintJob).where(PrintJob.id == job.id).values(status=S.CANCELLED)
                       .execution_options(synchronize_session=False))
    db.session.flush()
    assert stale.status == S.CLAIMED  # our copy is stale
    with pytest.raises(pj.JobTransitionError):
        pj.agent_report(rig.agent, stale, S.DOWNLOADING)


# ---- cancellation -------------------------------------------------------------------------------------------------

def test_cancel_queued_job_by_owner_and_admin(rig):
    j1, j2 = new_job(rig), new_job(rig)
    pj.cancel_job(j1, rig.worker)
    pj.cancel_job(j2, rig.admin)
    db.session.commit()
    assert j1.status == j2.status == S.CANCELLED and j1.finished_at is not None
    assert actions().count("print.cancelled") == 2
    with pytest.raises(ServiceError):
        pj.cancel_job(j1, rig.worker)


def test_cancel_claimed_job_stops_the_agent(rig):
    job = new_job(rig)
    _, token = claim(rig)
    pj.cancel_job(job, rig.worker)
    db.session.commit()
    with pytest.raises(pj.JobTransitionError):
        report(rig, job, token, S.DOWNLOADING)
    assert job.status == S.CANCELLED


def test_cannot_cancel_after_download_or_submission(rig):
    job, token = advance_to_downloaded(rig)
    assert not pj.can_cancel(rig.worker, job)
    with pytest.raises(ServiceError, match="already been sent"):
        pj.cancel_job(job, rig.worker)
    report(rig, job, token, S.SUBMITTED_TO_PRINTER)
    with pytest.raises(ServiceError):
        pj.cancel_job(job, rig.admin)


def test_other_worker_cannot_cancel(rig):
    job = new_job(rig)
    w2 = create_user(rig.company, email="w2@acme-corp.com", full_name="Two", password=PW, role=UserRole.WORKER)
    db.session.commit()
    with pytest.raises(NotFound):
        pj.cancel_job(job, w2)
    assert job.status == S.QUEUED


# ---- lease recovery -----------------------------------------------------------------------------------------------

def test_fresh_lease_is_left_alone(rig):
    new_job(rig)
    claim(rig)
    assert pj.recover_expired_leases() == 0


def test_expired_claim_is_requeued_then_failed_after_max_attempts(rig, app):
    job = new_job(rig)
    for attempt in range(app.config.get("MAX_JOB_ATTEMPTS", 3)):
        assert claim(rig) is not None
        expire(job)
        assert pj.recover_expired_leases() == 1
        db.session.commit()
        db.session.refresh(job)
        if attempt < 2:
            assert job.status == S.QUEUED and job.agent_id is None and job.claim_token_hash is None
    assert job.status == S.FAILED and job.error_code == "AGENT_LOST"
    assert actions().count("print.lease_requeued") == 2


def test_expired_downloading_job_is_requeued_not_unknown(rig):
    job = new_job(rig)
    _, token = claim(rig)
    report(rig, job, token, S.DOWNLOADING)
    expire(job)
    pj.recover_expired_leases()
    db.session.commit()
    assert job.status == S.QUEUED


def test_expired_downloaded_job_becomes_unknown_and_is_never_requeued(rig):
    job, _ = advance_to_downloaded(rig)
    expire(job)
    assert pj.recover_expired_leases() == 1
    db.session.commit()
    assert job.status == S.UNKNOWN and job.error_code == "AGENT_LOST"
    assert claim(rig) is None  # not offered to any agent again
    assert pj.recover_expired_leases() == 0


def test_original_agent_can_reconcile_unknown_job(rig):
    job, token = advance_to_downloaded(rig)
    expire(job)
    pj.recover_expired_leases()
    db.session.commit()
    report(rig, job, token, S.SUBMITTED_TO_PRINTER)  # agent's local journal proves it submitted
    assert job.status == S.SUBMITTED_TO_PRINTER and job.error_code is None


def test_renew_lease_extends_only_leased_jobs(rig):
    job = new_job(rig)
    with pytest.raises(pj.JobTransitionError):
        pj.renew_lease(rig.agent, job)  # still QUEUED
    _, token = claim(rig)
    expire(job)
    pj.renew_lease(rig.agent, pj.get_agent_job(rig.agent, job.public_id, token))
    db.session.commit()
    assert pj.recover_expired_leases() == 0 and job.status == S.CLAIMED


# ---- administrator resolution and resubmit ------------------------------------------------------------------------

def make_unknown(rig):
    job, _ = advance_to_downloaded(rig)
    expire(job)
    pj.recover_expired_leases()
    db.session.commit()
    return job


def test_admin_resolves_unknown_as_printed_or_not_printed(rig):
    j1 = make_unknown(rig)
    pj.resolve_unknown(j1, "printed", rig.admin, "paper in the tray")
    db.session.commit()
    assert j1.status == S.COMPLETED and j1.finished_at is not None and j1.error_code is None
    j2 = make_unknown(rig)
    pj.resolve_unknown(j2, "not_printed", rig.admin)
    db.session.commit()
    assert j2.status == S.FAILED and j2.error_code == "NOT_PRINTED_CONFIRMED"
    assert actions().count("print.resolved") == 2


def test_only_admins_resolve_and_only_unknown_jobs(rig):
    job = make_unknown(rig)
    with pytest.raises(NotFound):
        pj.resolve_unknown(job, "printed", rig.worker)
    with pytest.raises(ServiceError):
        pj.resolve_unknown(job, "maybe", rig.admin)
    queued = new_job(rig)
    with pytest.raises(ServiceError):
        pj.resolve_unknown(queued, "printed", rig.admin)


def test_resubmit_creates_new_job_only_after_failed_or_cancelled(rig):
    job = new_job(rig, copies=2, paper_size="A3")
    with pytest.raises(ServiceError):
        pj.resubmit(job, rig.worker)  # still queued
    pj.cancel_job(job, rig.worker)
    db.session.commit()
    new = pj.resubmit(job, rig.worker)
    db.session.commit()
    assert new.id != job.id and new.status == S.QUEUED and (new.copies, new.paper_size) == (2, "A3")
    pj.cancel_job(new, rig.worker)
    db.session.commit()
    unknown = make_unknown(rig)
    with pytest.raises(ServiceError):
        pj.resubmit(unknown, rig.worker)  # must be resolved by an administrator first
