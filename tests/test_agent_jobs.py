"""Agent job loop against the REAL Flask backend (Flask test client as transport) with a fake printing mechanism."""
import io
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from sqlalchemy import select

pytest.importorskip("requests")

from app.extensions import db  # noqa: E402
from app.models import Agent, ConvertedFile, PrintJob  # noqa: E402
from app.models import PrintJobStatus as S  # noqa: E402
from app.services import conversion_queue as cq  # noqa: E402
from app.services import print_jobs as pj  # noqa: E402
from app.services.agents import create_enrollment  # noqa: E402
from app.services.documents import ingest_upload  # noqa: E402
from app.services.offices import create_office  # noqa: E402
from office_agent.agent_service import AgentService, State  # noqa: E402
from office_agent.cloud_client import CloudClient  # noqa: E402
from office_agent.job_journal import JobJournal  # noqa: E402
from office_agent.job_processor import JobProcessor  # noqa: E402
from office_agent.local_database import LocalDatabase  # noqa: E402
from office_agent.print_submitter import (FakeSubmitter, JobOptions, PrintOutcomeUncertain, PrintSubmitError,  # noqa: E402
                                          SimulatedCrash, SumatraSubmitter, build_command, build_settings)
from office_agent.printer_manager import FakePrinterBackend, InstalledPrinter  # noqa: E402
from office_agent.secrets_store import CredentialStore, InsecureDevProtector  # noqa: E402
from tests.factories import make_docx  # noqa: E402

HP = InstalledPrinter("HP LaserJet", "AVAILABLE", False)


class _Resp:
    def __init__(self, r):
        self.status_code, self.headers, self._r = r.status_code, r.headers, r

    def json(self):
        data = self._r.get_json(silent=True)
        if data is None:
            raise ValueError("no json")
        return data

    def iter_content(self, n):
        data = self._r.data
        for i in range(0, len(data), n):
            yield data[i:i + n]

    def close(self):
        pass


class FlaskTransport:
    def __init__(self, client):
        self.client = client

    def request(self, method, url, json=None, headers=None, **kw):
        return _Resp(self.client.open(urlsplit(url).path, method=method, json=json, headers=headers))


class FakeUpload:
    def __init__(self, name, data):
        self.filename, self.mimetype, self.stream = name, "", io.BytesIO(data)


@pytest.fixture()
def rig(app, client, acme, tmp_path):
    company, admin, worker = acme
    office = create_office(company, name="HQ", location=None, actor=admin)
    _, token = create_enrollment(office, name="PC", actor=admin)
    db.session.commit()
    local = LocalDatabase(tmp_path / "agent.db")
    backend = FakePrinterBackend([HP])
    svc = AgentService(local, CredentialStore(local, InsecureDevProtector()), backend,
                       lambda url, cred: CloudClient(url, cred, session=FlaskTransport(client)), hostname="PC")
    svc.register("http://localhost", token)
    svc.set_enabled(["HP LaserJet"])
    svc.tick()
    assert svc.state == State.CONNECTED
    doc = ingest_upload(FakeUpload("a.docx", make_docx()), worker)
    cq.run_worker(once=True)
    db.session.refresh(doc)
    sub = FakeSubmitter()
    journal = JobJournal(local, InsecureDevProtector())
    work = tmp_path / "jobs"
    proc = JobProcessor(svc, journal, sub, work_dir=work)
    return SimpleNamespace(svc=svc, local=local, backend=backend, doc=doc, worker=worker, sub=sub, journal=journal, proc=proc,
                           work=work, company=company)


def queue(rig):
    job, _ = pj.create_print_job(rig.worker, rig.doc, copies=2, paper_size="Letter", landscape=True)
    db.session.commit()
    return job


def agent_model():
    return db.session.scalar(select(Agent))


def server_claim(job, journal=None, stage=None):
    """Claim (and optionally advance) directly on the server, as the agent would before it died."""
    j, token = pj.claim_next_job(agent_model())
    db.session.commit()
    return token


def server_move(job, token, *states):
    for s in states:
        pj.agent_report(agent_model(), pj.get_agent_job(agent_model(), job.public_id, token), s)
        db.session.commit()


def stage(rig, job):
    return rig.journal.stage(str(job.public_id))


# ---- the happy path and refusals ----------------------------------------------------------------------------------

def test_job_is_downloaded_verified_printed_and_reported(rig):
    job = queue(rig)
    assert rig.proc.run_once() is True
    db.session.refresh(job)
    assert job.status == S.SUBMITTED_TO_PRINTER and job.submitted_at is not None and job.finished_at is None  # not COMPLETED
    (opts, head), = rig.sub.calls
    assert opts == JobOptions("HP LaserJet", 2, "Letter", None, True) and head == b"%PDF-"
    assert stage(rig, job) == "DONE" and list(rig.work.glob("*.pdf")) == []
    assert rig.proc.run_once() is False  # queue empty


def test_paused_agent_takes_no_jobs(rig):
    job = queue(rig)
    rig.svc.set_paused(True)
    assert rig.proc.run_once() is False
    db.session.refresh(job)
    assert job.status == S.QUEUED and rig.sub.calls == []


def test_missing_printing_program_means_no_jobs_are_taken(rig):
    rig.sub.problem = lambda: "SumatraPDF.exe was not found."
    job = queue(rig)
    assert rig.proc.run_once() is False
    db.session.refresh(job)
    assert job.status == S.QUEUED
    assert any("SumatraPDF" in a["message"] for a in rig.local.recent_activity(10))


def test_checksum_mismatch_is_never_printed(rig):
    job = queue(rig)
    cf = db.session.get(ConvertedFile, job.converted_file_id)
    cf.sha256 = "0" * 64
    db.session.commit()
    rig.proc.run_once()
    db.session.refresh(job)
    assert job.status == S.FAILED and job.error_code == "CHECKSUM_MISMATCH" and rig.sub.calls == []
    assert list(rig.work.glob("*.pdf")) == [] and stage(rig, job) == "DONE"


def test_printer_removed_from_windows_fails_job_without_printing(rig):
    job = queue(rig)
    rig.backend.printers = []
    rig.proc.run_once()
    db.session.refresh(job)
    assert job.status == S.FAILED and job.error_code == "PRINTER_UNAVAILABLE" and rig.sub.calls == []


def test_printer_reported_offline_by_windows_fails_job(rig):
    job = queue(rig)
    rig.backend.printers = [InstalledPrinter("HP LaserJet", "OFFLINE", False)]
    rig.proc.run_once()
    db.session.refresh(job)
    assert job.status == S.FAILED and job.error_code == "PRINTER_UNAVAILABLE"


def test_print_command_failure_is_failed_and_uncertainty_is_unknown(rig):
    j1 = queue(rig)
    rig.sub.mode = "fail"
    rig.proc.run_once()
    db.session.refresh(j1)
    assert j1.status == S.FAILED and j1.error_code == "PRINT_FAILED"
    j2 = queue(rig)
    rig.sub.mode = "uncertain"
    rig.proc.run_once()
    db.session.refresh(j2)
    assert j2.status == S.UNKNOWN and j2.error_code == "OUTCOME_UNCERTAIN" and "Check the printer" in j2.error_message
    assert len(rig.sub.calls) == 2  # each job attempted exactly once


def test_job_cancelled_after_claim_is_not_printed(rig):
    job = queue(rig)
    claim = rig.svc._client().claim_job()
    pj.cancel_job(job, rig.worker)
    db.session.commit()
    rig.proc.handle(claim)
    db.session.refresh(job)
    assert job.status == S.CANCELLED and rig.sub.calls == [] and stage(rig, job) == "DONE"


def test_garbage_job_from_server_is_failed_not_crashed(rig):
    job = queue(rig)
    claim = rig.svc._client().claim_job()
    claim["copies"] = "lots"
    rig.proc.handle(claim)
    db.session.refresh(job)
    assert job.status == S.FAILED and job.error_code == "BAD_JOB" and rig.sub.calls == []


# ---- crash recovery -----------------------------------------------------------------------------------------------

def test_crash_while_submitting_is_reported_unknown_and_never_reprinted(rig, tmp_path):
    job = queue(rig)
    rig.sub.mode = "crash"
    with pytest.raises(SimulatedCrash):
        rig.proc.run_once()
    db.session.refresh(job)
    assert job.status == S.DOWNLOADED and stage(rig, job) == "SUBMITTING"
    sub2 = FakeSubmitter()
    restarted = JobProcessor(rig.svc, rig.journal, sub2, work_dir=rig.work)  # new process, same local database
    restarted.run_once()
    db.session.refresh(job)
    assert job.status == S.UNKNOWN and job.error_code == "AGENT_RESTARTED" and sub2.calls == []
    assert stage(rig, job) == "DONE"


def test_crash_after_downloaded_before_submitting_is_failed_not_printed(rig):
    job = queue(rig)
    token = server_claim(job)
    server_move(job, token, S.DOWNLOADING, S.DOWNLOADED)
    rig.journal.open_job(str(job.public_id), token, "HP LaserJet")
    rig.journal.advance(str(job.public_id), "DOWNLOADED")
    rig.proc.recover()
    db.session.refresh(job)
    assert job.status == S.FAILED and job.error_code == "AGENT_RESTARTED" and rig.sub.calls == []


def test_crash_after_claim_hands_job_back_to_queue(rig):
    job = queue(rig)
    token = server_claim(job)
    rig.journal.open_job(str(job.public_id), token, "HP LaserJet")
    rig.proc.recover()
    db.session.refresh(job)
    assert job.status == S.QUEUED and stage(rig, job) == "DONE"


def test_submitted_but_unreported_job_gets_its_report_resent(rig):
    job = queue(rig)
    token = server_claim(job)
    server_move(job, token, S.DOWNLOADING, S.DOWNLOADED)
    rig.journal.open_job(str(job.public_id), token, "HP LaserJet")
    rig.journal.advance(str(job.public_id), "SUBMITTED")
    rig.proc.recover()
    db.session.refresh(job)
    assert job.status == S.SUBMITTED_TO_PRINTER and rig.sub.calls == []


def test_server_unreachable_keeps_journal_for_later(rig):
    job = queue(rig)
    token = server_claim(job)
    rig.journal.open_job(str(job.public_id), token, "HP LaserJet")
    rig.svc._factory = lambda url, cred: (_ for _ in ()).throw(AssertionError("unused"))
    from office_agent.cloud_client import Unavailable
    original = rig.proc._report
    rig.proc._report = lambda *a, **k: "retry"
    rig.proc.recover()
    assert stage(rig, job) == "CLAIMED"  # still unsettled
    rig.proc._report = original


def test_claim_token_is_not_stored_in_clear(rig):
    job = queue(rig)
    token = server_claim(job)
    rig.journal.open_job(str(job.public_id), token, "HP LaserJet")
    assert token not in rig.local.job_get(str(job.public_id))["detail"]
    assert rig.journal.token(str(job.public_id)) == token


def test_journal_survives_restart_and_prunes_done_rows(tmp_path):
    p = tmp_path / "a.db"
    db1 = LocalDatabase(p)
    j = JobJournal(db1, InsecureDevProtector())
    j.open_job("a" * 8, "sj_secret", "HP")
    j.advance("a" * 8, "SUBMITTING")
    db1.close()
    j2 = JobJournal(LocalDatabase(p), InsecureDevProtector())
    assert j2.stage("a" * 8) == "SUBMITTING" and [r["job_id"] for r in j2.unfinished()] == ["a" * 8]
    for i in range(70):
        j2.open_job(f"job{i:04d}", "t", None)
        j2.finish(f"job{i:04d}")
    assert len(j2._db.jobs_not_in("NOPE")) <= 51  # 50 finished kept + the unfinished one


# ---- print command ------------------------------------------------------------------------------------------------

def test_sumatra_command_is_a_list_with_expected_settings():
    o = JobOptions('HP "Laser" Jet; rm', 3, "Letter", False, True)
    cmd = build_command("S.exe", Path("x.pdf"), o)
    assert cmd == ["S.exe", "-print-to", 'HP "Laser" Jet; rm', "-silent", "-print-settings", "3x,paper=letter,monochrome,landscape", "x.pdf"]
    assert build_settings(JobOptions("p")) == "1x,paper=A4"
    assert build_settings(JobOptions("p", 1, "A3", True, False)) == "1x,paper=A3,color,portrait"


@pytest.mark.parametrize("effect,exc", [(0, None), (1, PrintSubmitError), (FileNotFoundError(), PrintSubmitError),
                                        (subprocess.TimeoutExpired("x", 1), PrintOutcomeUncertain), (RuntimeError("boom"), PrintOutcomeUncertain)])
def test_sumatra_outcome_classification(tmp_path, effect, exc):
    exe = tmp_path / "S.exe"
    exe.write_bytes(b"x")

    def runner(cmd, **kw):
        if isinstance(effect, BaseException):
            raise effect
        return SimpleNamespace(returncode=effect)
    s = SumatraSubmitter(str(exe), runner=runner)
    assert s.problem() is None
    if exc is None:
        s.submit(tmp_path / "x.pdf", JobOptions("p"))
    else:
        with pytest.raises(exc):
            s.submit(tmp_path / "x.pdf", JobOptions("p"))


def test_sumatra_missing_is_reported_as_a_problem(monkeypatch):
    monkeypatch.setattr("office_agent.print_submitter.find_sumatra", lambda: None)
    assert "SumatraPDF" in SumatraSubmitter().problem()
