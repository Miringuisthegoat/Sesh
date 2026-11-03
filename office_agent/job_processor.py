"""Job loop of the Sesh Agent (Phase 5b): claim -> download -> verify SHA-256 -> tell server -> print -> report.

Safety rules:
* The server accepting DOWNLOADED is the commit point: the agent prints only after that call succeeded (so a cancel can
  never race with a print), and writes SUBMITTING to the local journal BEFORE running the print command.
* Anything uncertain is reported UNKNOWN and left for a human. Nothing is ever printed twice automatically.
* Recovery (start of every pass) settles whatever the journal says was left unfinished by a crash or network loss.
No Windows imports here: the printing mechanism is injected, so the whole loop is testable without Windows."""
from __future__ import annotations

import logging
import re
import threading
import uuid
from pathlib import Path

from . import backoff
from .agent_service import AgentService, State
from .cloud_client import ChecksumMismatch, CloudError, Rejected, Unauthorized, Unavailable
from .config import data_dir
from .job_journal import JobJournal
from .print_submitter import PAPER, JobOptions, PrintOutcomeUncertain, PrintSubmitError

log = logging.getLogger(__name__)
POLL_SECONDS = 5.0
MAX_PDF_BYTES = 100 * 1024 * 1024
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")


def _short(job_id: str) -> str:
    return job_id[:8]


class JobProcessor:
    def __init__(self, service: AgentService, journal: JobJournal, submitter, *, work_dir: Path | None = None,
                 poll_seconds: float = POLL_SECONDS):
        self.svc, self.journal, self.submitter, self.poll = service, journal, submitter, poll_seconds
        self.work = Path(work_dir) if work_dir else data_dir() / "jobs"
        self.work.mkdir(parents=True, exist_ok=True)
        for f in self.work.glob("*.pdf"):  # leftovers of a crash; nothing is in progress at start-up
            f.unlink(missing_ok=True)
        self.failures, self._problem_shown = 0, None
        self._stop = threading.Event()

    # ---- helpers ------------------------------------------------------------------------------------------------
    def _client(self):
        return self.svc._client()

    def _note(self, level: str, message: str) -> None:
        (log.warning if level != "info" else log.info)(message)
        self.svc.db.log(level, message)
        self.svc._notify()

    def _report(self, job_id: str, token: str, status: str, code: str | None = None, message: str | None = None) -> str:
        """'ok' (server accepted), 'gone' (server says it is not ours / illegal / cancelled) or 'retry' (could not reach it)."""
        try:
            self._client().report_status(job_id, token, status, code, message)
            return "ok"
        except Unavailable:
            return "retry"
        except Rejected:
            return "gone"

    def _finish(self, job_id: str) -> None:
        self.journal.finish(job_id)

    def _fail(self, job_id: str, token: str, code: str, message: str) -> None:
        res = self._report(job_id, token, "FAILED", code, message)
        if res != "retry":
            self._finish(job_id)
        self._note("error", f"Job {_short(job_id)} failed: {message}")

    @staticmethod
    def _parse(claim: dict) -> tuple[JobOptions, str, int | None]:
        """Validate the server's job description. Raises ValueError with a safe message."""
        copies, paper = claim.get("copies"), claim.get("paper_size")
        color, land, printer = claim.get("color"), claim.get("landscape"), claim.get("printer")
        sha, size = claim.get("sha256"), claim.get("size_bytes")
        if not isinstance(printer, str) or not printer:
            raise ValueError("missing printer")
        if not isinstance(copies, int) or isinstance(copies, bool) or not 1 <= copies <= 99:
            raise ValueError("invalid copies")
        if paper not in PAPER:
            raise ValueError("invalid paper size")
        if any(v is not None and not isinstance(v, bool) for v in (color, land)):
            raise ValueError("invalid options")
        if not isinstance(sha, str) or not _HEX64.match(sha):
            raise ValueError("invalid checksum")
        if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size < 1):
            raise ValueError("invalid size")
        return JobOptions(printer, copies, paper, color, land), sha.lower(), size

    # ---- recovery -----------------------------------------------------------------------------------------------
    def recover(self) -> None:
        """Settle jobs left unfinished by a crash or an unreachable server. Safe to call repeatedly."""
        for rec in self.journal.unfinished():
            jid, stage = rec["job_id"], rec["stage"]
            token = self.journal.token(jid)
            if not token:
                self._finish(jid)
                continue
            if stage in ("CLAIMED", "DOWNLOADING"):  # nothing can have been printed: hand it back
                res = self._report(jid, token, "QUEUED")
            elif stage == "DOWNLOADED":  # not yet sent to the printer; the lease is gone, so do not start now
                res = self._report(jid, token, "FAILED", "AGENT_RESTARTED",
                                   "The office computer restarted before printing. Nothing was printed; submit the document again.")
            elif stage == "SUBMITTING":  # the print command may have run: uncertain, never reprint
                res = self._report(jid, token, "UNKNOWN", "AGENT_RESTARTED",
                                   "The office computer restarted while sending this job to the printer. Check the printer to see whether it printed.")
            elif stage == "SUBMITTED":  # printed; only the report was missing
                res = self._report(jid, token, "SUBMITTED_TO_PRINTER")
            else:
                res = "gone"
            if res != "retry":
                self._finish(jid)
                self._note("warning", f"Job {_short(jid)} was found unfinished after an interruption ({stage}) and has been settled.")

    # ---- one job ------------------------------------------------------------------------------------------------
    def handle(self, claim: dict) -> None:
        jid, token = claim.get("id"), claim.get("claim_token")
        try:
            uuid.UUID(str(jid))
        except ValueError:
            jid = None
        if not jid or not isinstance(token, str) or not token:
            self._note("error", "The server sent a job this computer could not understand; it was ignored.")
            return
        self.journal.open_job(jid, token, claim.get("printer") if isinstance(claim.get("printer"), str) else None)
        try:
            opts, sha, size = self._parse(claim)
        except ValueError as e:
            self._fail(jid, token, "BAD_JOB", f"The job description was not valid ({e}).")
            return
        self.svc.refresh_printers()
        view = {p["name"]: p for p in self.svc.printers_view()}.get(opts.printer)
        if view is None or not view["installed"] or not view["enabled"] or view["status"] == "OFFLINE":
            self._fail(jid, token, "PRINTER_UNAVAILABLE", "The office printer is not available on the office computer.")
            return

        res = self._report(jid, token, "DOWNLOADING")
        if res == "gone":
            self._finish(jid)  # cancelled or lost: do nothing
            return
        if res == "retry":
            return  # journal stays CLAIMED; recovery hands the job back
        self.journal.advance(jid, "DOWNLOADING")

        path = self.work / f"{jid}.pdf"
        try:
            try:
                self._client().download_pdf(jid, token, path, sha, size, MAX_PDF_BYTES)
            except ChecksumMismatch:
                self._fail(jid, token, "CHECKSUM_MISMATCH", "The downloaded PDF did not match its checksum, so it was not printed.")
                return
            except Rejected:
                self._finish(jid)
                return
            except Unavailable:
                return  # recovery hands the job back
            if not path.read_bytes()[:5] == b"%PDF-":
                self._fail(jid, token, "NOT_A_PDF", "The downloaded file is not a PDF, so it was not printed.")
                return
            self.journal.advance(jid, "DOWNLOADED")  # before telling the server: a crash here is settled as FAILED, never printed
            res = self._report(jid, token, "DOWNLOADED")
            if res == "gone":
                self._finish(jid)
                return
            if res == "retry":
                return  # not confirmed: do not print; recovery fails it safely
            self.journal.advance(jid, "SUBMITTING")  # write-ahead: from here on the outcome can only be known or UNKNOWN
            try:
                self.submitter.submit(path, opts)
            except PrintSubmitError as e:
                self._fail(jid, token, "PRINT_FAILED", str(e))
                return
            except PrintOutcomeUncertain as e:
                self._report(jid, token, "UNKNOWN", "OUTCOME_UNCERTAIN", f"{e} Check the printer to see whether it printed.")
                self._finish(jid)
                self._note("warning", f"Job {_short(jid)}: not certain whether it printed.")
                return
            self.journal.advance(jid, "SUBMITTED")
            if self._report(jid, token, "SUBMITTED_TO_PRINTER") != "retry":
                self._finish(jid)
            self._note("info", f"Job {_short(jid)} sent to {opts.printer} (Windows accepted it; not proof that paper came out).")
        finally:
            path.unlink(missing_ok=True)

    # ---- loop ---------------------------------------------------------------------------------------------------
    def run_once(self) -> bool:
        """Recover, then claim and handle at most one job. True if a job was handled (call again at once)."""
        if self.svc.state in (State.NOT_REGISTERED, State.REVOKED):
            return False
        try:
            self.recover()
            if self.svc.paused or self.svc.state != State.CONNECTED:
                return False
            problem = self.submitter.problem()
            if problem:
                if problem != self._problem_shown:
                    self._problem_shown = problem
                    self._note("error", problem)
                return False
            self._problem_shown = None
            claim = self._client().claim_job()
            self.failures = 0
            if claim is None:
                return False
            self.handle(claim)
            return True
        except Unauthorized:
            self.svc._mark_revoked()
            return False
        except CloudError:
            self.failures += 1
            return False

    def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                busy = self.run_once()
            except Exception:  # never let the loop die; the journal settles anything left half-done
                log.exception("job loop error")
                self.failures += 1
                busy = False
            if busy:
                continue
            self._stop.wait(backoff.delay(self.failures) if self.failures else self.poll)

    def stop(self) -> None:
        self._stop.set()
