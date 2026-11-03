"""Run once from the project root after unzipping the overlay: python apply_phase5b.py
Targeted edits to existing files plus appended code/docs. Idempotent; stops loudly if an anchor is missing."""
from pathlib import Path

EDITS = [
    # ---- server: agent job endpoints ------------------------------------------------------------------------------
    ("app/agents/routes.py", "from flask import current_app, g, jsonify, request\n",
     "from flask import Response, current_app, g, jsonify, request, stream_with_context\n"),
    ("app/agents/routes.py", "from ..models import PrinterAvailability\n",
     "from ..models import PrinterAvailability, PrintJobStatus\n"),
    ("app/agents/routes.py", "from ..services.errors import ServiceError\nfrom . import bp\n",
     "from ..services import print_jobs as pj\nfrom ..services.errors import ServiceError\n"
     "from ..services.storage import StorageError, get_storage\nfrom . import bp\n"),
    # ---- agent: local journal table access -------------------------------------------------------------------------
    ("office_agent/local_database.py", "    def close(self) -> None:\n        with self._lock:\n            self._conn.close()\n",
     '''    # ---- job-recovery journal (Phase 5b); see job_journal.py -------------------------------------------------
    def job_save(self, job_id: str, stage: str, printer: str | None = None, detail: str | None = None) -> None:
        self._run("INSERT INTO jobs(job_id,stage,printer,detail,updated_at) VALUES(?,?,?,?,?) "
                  "ON CONFLICT(job_id) DO UPDATE SET stage=excluded.stage, printer=COALESCE(excluded.printer, printer), "
                  "detail=COALESCE(excluded.detail, detail), updated_at=excluded.updated_at", (job_id, stage, printer, detail, _now()))

    def job_get(self, job_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT job_id,stage,printer,detail,updated_at FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def jobs_not_in(self, stage: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT job_id,stage,printer,detail,updated_at FROM jobs WHERE stage<>? ORDER BY updated_at", (stage,)).fetchall()
        return [dict(r) for r in rows]

    def jobs_prune(self, keep: int = 50) -> None:
        self._run("DELETE FROM jobs WHERE stage='DONE' AND job_id NOT IN "
                  "(SELECT job_id FROM jobs WHERE stage='DONE' ORDER BY updated_at DESC LIMIT ?)", (keep,))

    def close(self) -> None:
        with self._lock:
            self._conn.close()
'''),
    # ---- agent: cloud client ---------------------------------------------------------------------------------------
    ("office_agent/cloud_client.py", "from .config import VERSION, normalize_server_url\n",
     "import hashlib\nfrom pathlib import Path\n\nfrom .config import VERSION, normalize_server_url\n"),
    ("office_agent/cloud_client.py", "class CloudClient:\n",
     '''class ChecksumMismatch(CloudError):
    """The downloaded bytes do not match the checksum/size the server announced. The file must not be printed."""


class CloudClient:
'''),
    ("office_agent/cloud_client.py", '    def unregister(self) -> None:\n        self._call("POST", "/unregister")\n',
     '''    def unregister(self) -> None:
        self._call("POST", "/unregister")

    # ---- print jobs (Phase 5b) ---------------------------------------------------------------------------------
    def claim_job(self) -> dict | None:
        job = self._call("POST", "/jobs/claim").get("job")
        return job if isinstance(job, dict) else None

    def report_status(self, job_id: str, claim_token: str, status: str, code: str | None = None, message: str | None = None) -> None:
        self._call("POST", f"/jobs/{job_id}/status", {"claim_token": claim_token, "status": status, "error_code": code, "error_message": message})

    def renew_lease(self, job_id: str, claim_token: str) -> None:
        self._call("POST", f"/jobs/{job_id}/lease", {"claim_token": claim_token})

    def download_pdf(self, job_id: str, claim_token: str, dest: Path, expected_sha256: str, expected_size: int | None,
                     max_bytes: int) -> None:
        """Stream the job's PDF to `dest`, hashing as it arrives. Raises ChecksumMismatch (file deleted) if bytes differ."""
        import requests
        headers = {"User-Agent": f"SeshAgent/{VERSION}", "Authorization": "Bearer " + (self._credential or ""), "X-Claim-Token": claim_token}
        try:
            r = self._sess().request("GET", f"{self.base}/jobs/{job_id}/pdf", headers=headers, timeout=(5, 60),
                                     allow_redirects=False, stream=True)
        except requests.RequestException as e:
            raise Unavailable("NETWORK", "Cannot reach the Sesh server. Check the internet connection.") from e
        try:
            status = r.status_code
            if 300 <= status < 400:
                raise Unavailable("REDIRECT", "The server address redirected the agent.", status)
            if status != 200:
                try:
                    data = r.json()
                except ValueError:
                    data = None
                err = data.get("error", {}) if isinstance(data, dict) and isinstance(data.get("error"), dict) else {}
                code, msg = str(err.get("code", "ERROR")), str(err.get("message", f"The server returned an error ({status})."))[:300]
                if status == 401:
                    raise Unauthorized(code, msg, status)
                if status == 429 or status >= 500:
                    raise Unavailable(code, msg, status)
                raise Rejected(code, msg, status)
            digest, size = hashlib.sha256(), 0
            try:
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(65536):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > max_bytes:
                            raise ChecksumMismatch("TOO_LARGE", "The PDF is larger than this computer accepts.")
                        digest.update(chunk)
                        f.write(chunk)
            except requests.RequestException as e:
                Path(dest).unlink(missing_ok=True)
                raise Unavailable("NETWORK", "The download was interrupted.") from e
            except ChecksumMismatch:
                Path(dest).unlink(missing_ok=True)
                raise
        finally:
            getattr(r, "close", lambda: None)()
        if digest.hexdigest() != expected_sha256.lower() or (expected_size is not None and size != expected_size):
            Path(dest).unlink(missing_ok=True)
            raise ChecksumMismatch("CHECKSUM", "The downloaded PDF does not match its checksum.")
'''),
    # ---- agent: start the job loop ---------------------------------------------------------------------------------
    ("office_agent/main.py", '    threading.Thread(target=service.run_forever, name="heartbeat", daemon=True).start()\n',
     '''    threading.Thread(target=service.run_forever, name="heartbeat", daemon=True).start()
    from .job_journal import JobJournal
    from .job_processor import JobProcessor
    from .print_submitter import FakeSubmitter, SumatraSubmitter
    submitter = FakeSubmitter() if os.environ.get("SESH_AGENT_FAKE_PRINTERS") == "1" else SumatraSubmitter()
    processor = JobProcessor(service, JobJournal(db, default_protector()), submitter)
    threading.Thread(target=processor.run_forever, name="jobs", daemon=True).start()
'''),
]

ROUTES_APPEND = '''

# ---- print jobs (Phase 5b) ------------------------------------------------------------------------------------------

def _job_err(code: str, message: str, status: int, job=None):
    body = {"code": code, "message": message}
    if job is not None:
        body["job_status"] = job.status.value
    return jsonify(error=body), status


def _held_job(public_id, token):
    """(job, None) if this agent holds the job and the claim token matches, else (None, 404 response)."""
    try:
        return pj.get_agent_job(g.agent, public_id, token if isinstance(token, str) else None), None
    except pj.JobAuthError:
        return None, _err("JOB_NOT_FOUND", "Unknown job, or it is not held by this agent.", 404)


def _job_state(job):
    return {"id": str(job.public_id), "status": job.status.value}


@bp.post("/jobs/claim")
@agent_required
def claim_job():
    res = pj.claim_next_job(g.agent)
    db.session.commit()
    if res is None:
        return jsonify(job=None)
    job, token = res
    cf = job.converted_file
    return jsonify(job={"id": str(job.public_id), "claim_token": token, "printer": job.printer.windows_name, "copies": job.copies,
                        "paper_size": job.paper_size, "color": job.color, "landscape": job.landscape, "sha256": cf.sha256,
                        "size_bytes": cf.size_bytes, "pages": cf.page_count, "lease_seconds": current_app.config["JOB_LEASE_SECONDS"]})


@bp.get("/jobs/<uuid:public_id>/pdf")
@agent_required
def job_pdf(public_id):
    """Authenticated streaming of the job's PDF to the agent holding it. No public or permanent URL exists."""
    job, err = _held_job(public_id, request.headers.get("X-Claim-Token"))
    if err:
        return err
    try:
        pj.renew_lease(g.agent, job)
    except pj.JobTransitionError as e:
        db.session.rollback()
        return _job_err("JOB_NOT_AVAILABLE", str(e), 409, job)
    cf = job.converted_file
    try:
        size, chunks = get_storage().open_stream(cf.storage_key)
    except StorageError:
        db.session.rollback()
        return _err("STORAGE_UNAVAILABLE", "The file could not be read right now.", 503)
    audit("print.pdf_downloaded", actor_agent=g.agent, target_type="print_job", target_id=job.public_id)
    db.session.commit()
    resp = Response(stream_with_context(chunks), mimetype="application/pdf")
    resp.headers["Content-Length"] = str(size)
    resp.headers["X-Content-SHA256"] = cf.sha256 or ""
    return resp


@bp.post("/jobs/<uuid:public_id>/status")
@agent_required
def job_status(public_id):
    data = _body()
    if data is None or not isinstance(data.get("status"), str):
        return _err("BAD_REQUEST", "claim_token and status are required.", 400)
    for k in ("error_code", "error_message"):
        if data.get(k) is not None and not isinstance(data[k], str):
            return _err("BAD_REQUEST", f"{k} must be text.", 400)
    try:
        to = PrintJobStatus(data["status"].upper())
    except ValueError:
        return _err("BAD_REQUEST", "Unknown status.", 400)
    job, err = _held_job(public_id, data.get("claim_token"))
    if err:
        return err
    try:
        pj.agent_report(g.agent, job, to, error_code=data.get("error_code"), error_message=data.get("error_message"))
        db.session.commit()
    except pj.JobTransitionError as e:
        db.session.rollback()
        code = "JOB_CANCELLED" if job.status == PrintJobStatus.CANCELLED else "ILLEGAL_TRANSITION"
        return _job_err(code, str(e), 409, job)
    return jsonify(job=_job_state(job))


@bp.post("/jobs/<uuid:public_id>/lease")
@agent_required
def job_lease(public_id):
    data = _body() or {}
    job, err = _held_job(public_id, data.get("claim_token"))
    if err:
        return err
    try:
        pj.renew_lease(g.agent, job)
        db.session.commit()
    except pj.JobTransitionError as e:
        db.session.rollback()
        return _job_err("ILLEGAL_TRANSITION", str(e), 409, job)
    return jsonify(job=_job_state(job))
'''

API_DOC = '''

## Print jobs (Phase 5b)
All bearer-authenticated like the calls above. A job is identified by its public id; the **claim token** returned by `claim` proves this agent holds it (only its hash is stored server-side).

| Call | Purpose |
|---|---|
| `POST /jobs/claim` | Atomically take the oldest queued job for this office/printers. `{job: null}` if none, else `{job: {id, claim_token, printer, copies, paper_size, color, landscape, sha256, size_bytes, pages, lease_seconds}}`. The token is shown once. |
| `GET /jobs/<id>/pdf` + header `X-Claim-Token` | Streams the PDF (`X-Content-SHA256` header). Only while the agent holds the job (CLAIMED/DOWNLOADING/DOWNLOADED); renews the lease. |
| `POST /jobs/<id>/status` `{claim_token, status, error_code?, error_message?}` | Report DOWNLOADING, DOWNLOADED, SUBMITTED_TO_PRINTER, COMPLETED, FAILED, UNKNOWN, or QUEUED (hand the job back). Repeating the current status is a no-op. Each report renews the lease. |
| `POST /jobs/<id>/lease` `{claim_token}` | Renew the lease without changing status. |

Errors: `404 JOB_NOT_FOUND` (unknown, not held, wrong token, or already handed back), `409 JOB_CANCELLED` / `ILLEGAL_TRANSITION` / `JOB_NOT_AVAILABLE` (body includes `job_status`). On 404 or 409 the agent must stop working on that job and never print it.
Lease: if the agent stops reporting for `JOB_LEASE_SECONDS`, CLAIMED/DOWNLOADING jobs are requeued, DOWNLOADED jobs become UNKNOWN (never reprinted automatically).
'''

AGENT_DOC = '''

## Printing (Phase 5b)
The agent now takes jobs: claim -> download -> verify SHA-256 -> tell the server (DOWNLOADED) -> print with SumatraPDF -> report SUBMITTED_TO_PRINTER. That status means **Windows accepted the job**, not that paper came out.
**SumatraPDF is not bundled** (licence review open, decision 4). Install it, or put `SumatraPDF.exe` next to `SeshAgent.exe` or in `%LOCALAPPDATA%\\SeshAgent\\bin`, or set `SUMATRA_PATH`. Without it the agent takes no jobs and says so in the activity log.
Pause printing stops new claims (recovery still runs). The printer must be installed, ticked and not reported offline by Windows, otherwise the job fails with PRINTER_UNAVAILABLE.
Crash/restart recovery (journal in `agent.db`): CLAIMED/DOWNLOADING -> handed back to the queue; DOWNLOADED -> FAILED (nothing printed; submit again); SUBMITTING -> UNKNOWN (an administrator checks the printer); SUBMITTED -> report resent. Nothing is ever printed twice automatically.
Real-printer checklist (NOT yet done): print a 1-page docx and a multi-page xlsx; check copies, A4/Letter, colour/mono, landscape; unplug the printer mid-job; kill the agent during printing and confirm UNKNOWN; cancel a queued job; test with the printer off.
'''

ROWS = """| 43 | 2026-11-03 | Agent job API under /api/agent/v1/jobs: claim, pdf (X-Claim-Token header), status, lease. 404 JOB_NOT_FOUND hides unknown/foreign/wrong-token jobs; 409 JOB_CANCELLED / ILLEGAL_TRANSITION carries job_status | Agent can always tell 'stop, do not print' from 'try later' |
| 44 | 2026-11-03 | Agent journals every stage locally (write-ahead); SUBMITTING is written before the print command; the claim token is stored DPAPI-protected | A crash can never cause a blind reprint |
| 45 | 2026-11-03 | Recovery: CLAIMED/DOWNLOADING -> hand back; DOWNLOADED -> FAILED AGENT_RESTARTED; SUBMITTING -> UNKNOWN; SUBMITTED -> resend report. DOWNLOADED is journalled before it is reported, so a crash in between is settled as FAILED, never printed | Conservative: user may have to resubmit, but no duplicates |
| 46 | 2026-11-03 | SumatraPDF: cannot start or non-zero exit -> FAILED; timeout/other failure after start -> UNKNOWN; not installed -> agent takes no jobs. Not bundled | Truthful status; licence review still open |
| 47 | 2026-11-03 | Before printing the agent re-reads Windows printers; not installed, not enabled or reported OFFLINE -> FAILED PRINTER_UNAVAILABLE (no silent waiting) | Fast, honest feedback; Windows' ERROR status is only a hint so it does not block |
"""


def main():
    for path, old, new in EDITS:
        p = Path(path)
        s = p.read_text(encoding="utf-8")
        if new in s:
            print("already applied:", path, repr(old[:40]))
            continue
        if s.count(old) != 1:
            raise SystemExit(f"ANCHOR NOT FOUND (or not unique) in {path}: {old[:60]!r}")
        p.write_text(s.replace(old, new), encoding="utf-8")
        print("patched:", path)
    for path, marker, text in (("app/agents/routes.py", "def claim_job", ROUTES_APPEND), ("docs/AGENT_API.md", "## Print jobs (Phase 5b)", API_DOC),
                               ("docs/AGENT.md", "## Printing (Phase 5b)", AGENT_DOC), ("docs/DECISIONS.md", "| 43 |", ROWS)):
        p = Path(path)
        s = p.read_text(encoding="utf-8")
        if marker in s:
            print("already applied:", path, marker)
            continue
        if not s.endswith("\n"):
            s += "\n"
        p.write_text(s + (text if path != "docs/DECISIONS.md" else text), encoding="utf-8")
        print("appended:", path)


if __name__ == "__main__":
    main()
