"""Print-job core (Phase 5a): creation, state machine, atomic claiming, lease recovery, cancellation, resolution.

Printing is a physical side effect, so the rules are conservative:
* Every status change goes through `_move`, which checks the transition table AND updates with a conditional
  UPDATE (`WHERE status = <what we saw>`), so two actors can never both win a race.
* An agent claims a job with a one-time claim token (only its SHA-256 is stored). Every agent update must present it.
* Each agent report renews the lease. If an agent disappears, CLAIMED/DOWNLOADING jobs go back to the queue
  (nothing can have been printed yet); a DOWNLOADED job becomes UNKNOWN (the print command may already have run)
  and is NEVER re-printed automatically.
* SUBMITTED_TO_PRINTER means "Windows accepted it", not "paper came out". COMPLETED needs a reliable signal
  (an administrator's confirmation, or an agent that can prove it).
Services never commit except where stated; the caller (route / CLI) commits.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from flask import abort, current_app
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import PAPER_SIZES, Agent, AgentStatus, AuditLog, PrintJob
from ..models import PrintJobStatus as S
from ..models.base import utcnow
from .agents import active_printers
from .audit import audit
from .documents import latest_pdf
from .errors import ServiceError
from .offices import get_company_office


class JobTransitionError(ServiceError):
    """Illegal or lost-race status change. The message is safe to show."""


class JobAuthError(Exception):
    """Unknown job, job not held by this agent, or wrong claim token. Deliberately carries no detail."""


# ---- state machine ------------------------------------------------------------------------------------------------

TRANSITIONS: dict[S, frozenset[S]] = {
    S.QUEUED: frozenset({S.CLAIMED, S.CANCELLED, S.FAILED}),
    S.CLAIMED: frozenset({S.DOWNLOADING, S.QUEUED, S.FAILED, S.CANCELLED}),
    S.DOWNLOADING: frozenset({S.DOWNLOADED, S.QUEUED, S.FAILED, S.CANCELLED}),
    S.DOWNLOADED: frozenset({S.SUBMITTED_TO_PRINTER, S.FAILED, S.UNKNOWN}),
    S.SUBMITTED_TO_PRINTER: frozenset({S.COMPLETED, S.FAILED, S.UNKNOWN}),
    S.UNKNOWN: frozenset({S.SUBMITTED_TO_PRINTER, S.COMPLETED, S.FAILED}),
    S.COMPLETED: frozenset(),
    S.FAILED: frozenset(),
    S.CANCELLED: frozenset(),
}
TERMINAL = frozenset({S.COMPLETED, S.FAILED, S.CANCELLED})
LEASED = frozenset({S.CLAIMED, S.DOWNLOADING, S.DOWNLOADED})            # an agent holds the job and its lease ticks
PENDING = frozenset({S.QUEUED, S.CLAIMED, S.DOWNLOADING, S.DOWNLOADED})  # not yet handed to Windows
USER_CANCELLABLE = frozenset({S.QUEUED, S.CLAIMED, S.DOWNLOADING})       # DOWNLOADED is the point of no return
AGENT_TARGETS = frozenset({S.DOWNLOADING, S.DOWNLOADED, S.SUBMITTED_TO_PRINTER, S.COMPLETED, S.FAILED, S.UNKNOWN, S.QUEUED})


def can_transition(frm: S, to: S) -> bool:
    return to in TRANSITIONS[frm]


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _lease() -> timedelta:
    return timedelta(seconds=current_app.config["JOB_LEASE_SECONDS"])


def _move(job: PrintJob, to: S, values: dict | None = None, *conds) -> None:
    """Validate the transition, then apply it with a conditional UPDATE. Raises JobTransitionError on any refusal."""
    frm = job.status
    if to not in TRANSITIONS[frm]:
        raise JobTransitionError(f"A job that is {frm.value} cannot become {to.value}.")
    res = db.session.execute(
        update(PrintJob).where(PrintJob.id == job.id, PrintJob.status == frm, *conds)
        .values(status=to, **(values or {})).execution_options(synchronize_session=False))
    if res.rowcount != 1:
        db.session.refresh(job)
        raise JobTransitionError("The job was changed by someone else. Reload and try again.")
    db.session.refresh(job)


# ---- access rules -------------------------------------------------------------------------------------------------

def visible_print_jobs(user):
    """Admins: every job in the company. Workers: only the jobs they requested."""
    q = select(PrintJob).where(PrintJob.company_id == user.company_id)
    if not user.is_admin:
        q = q.where(PrintJob.requested_by_id == user.id)
    return q


def get_print_job_or_404(public_id, user) -> PrintJob:
    job = db.session.scalar(visible_print_jobs(user).where(PrintJob.public_id == public_id))
    if job is None:
        abort(404)  # same answer for "missing", "other company" and "not yours"
    return job


def can_cancel(user, job: PrintJob) -> bool:
    return (job.company_id == user.company_id and (user.is_admin or job.requested_by_id == user.id)
            and job.status in USER_CANCELLABLE)


# ---- creation -----------------------------------------------------------------------------------------------------

def create_print_job(user, doc, *, copies: int = 1, paper_size: str | None = None, color: bool | None = None,
                     landscape: bool | None = None, idempotency_key: str | None = None) -> tuple[PrintJob, bool]:
    """Queue a print job for the document's latest PDF. Returns (job, created). A repeated idempotency key from the
    same user returns the original job (created=False), so a double-click or retried request prints once.
    The caller must already have checked that `user` may see `doc`."""
    cfg = current_app.config
    if doc.company_id != user.company_id:
        abort(404)
    if doc.deleted_at is not None:
        raise ServiceError("This document was removed, so it cannot be printed.")
    cf = latest_pdf(doc)
    if cf is None or not cf.storage_key:
        raise ServiceError("This document has not been converted to PDF yet.")

    key = (idempotency_key or "").strip()[:64] or None
    if key:
        existing = _by_key(user, key)
        if existing is not None:
            return _same_or_error(existing, cf), False

    company = user.company
    ceiling = min(company.max_copies, cfg["MAX_COPIES_LIMIT"])
    if not isinstance(copies, int) or isinstance(copies, bool) or not 1 <= copies <= ceiling:
        raise ServiceError(f"Copies must be a whole number between 1 and {ceiling}.")
    size = paper_size or company.default_paper_size
    if size not in PAPER_SIZES:
        raise ServiceError("That paper size is not supported.")
    for name, value in (("colour", color), ("landscape", landscape)):
        if value is not None and not isinstance(value, bool):
            raise ServiceError(f"Invalid {name} option.")

    office = get_company_office(user.company_id)
    if office is None:
        raise ServiceError("The office is not set up yet. Ask your administrator.")
    agent = db.session.scalar(select(Agent).where(Agent.company_id == user.company_id, Agent.office_id == office.id,
                                                  Agent.status == AgentStatus.ACTIVE))
    if agent is None:
        raise ServiceError("No office computer is registered yet. Ask your administrator.")
    printers = active_printers(agent)
    if not printers:
        raise ServiceError("No printer has been selected on the office computer yet.")
    printer = printers[0]
    if color is True and printer.supports_color is False:
        raise ServiceError("The office printer does not support colour printing.")

    pending = db.session.scalar(select(func.count(PrintJob.id)).where(PrintJob.requested_by_id == user.id,
                                                                      PrintJob.status.in_(PENDING)))
    if pending >= cfg.get("MAX_PENDING_JOBS_PER_USER", 20):
        raise ServiceError("You have too many jobs waiting. Wait for some to finish or cancel them.")

    job = PrintJob(company_id=user.company_id, office_id=office.id, requested_by_id=user.id, converted_file_id=cf.id,
                   printer_id=printer.id, status=S.QUEUED, copies=copies, paper_size=size, color=color,
                   landscape=landscape, idempotency_key=key)
    try:
        with db.session.begin_nested():
            db.session.add(job)
            db.session.flush()
    except IntegrityError:  # a concurrent request with the same key won the race
        existing = _by_key(user, key) if key else None
        if existing is None:
            raise
        return _same_or_error(existing, cf), False
    audit("print.created", actor=user, target_type="print_job", target_id=job.public_id,
          details={"copies": copies, "paper_size": size, "pages": cf.page_count})
    return job, True


def _by_key(user, key: str) -> PrintJob | None:
    return db.session.scalar(select(PrintJob).where(PrintJob.requested_by_id == user.id, PrintJob.idempotency_key == key))


def _same_or_error(existing: PrintJob, cf) -> PrintJob:
    if existing.converted_file_id != cf.id:
        raise ServiceError("This request id was already used for a different document.")
    return existing


def resubmit(job: PrintJob, user) -> PrintJob:
    """New job with the same options after a FAILED or CANCELLED one. UNKNOWN jobs must be resolved first."""
    if job.company_id != user.company_id or not (user.is_admin or job.requested_by_id == user.id):
        abort(404)
    if job.status not in (S.FAILED, S.CANCELLED):
        raise ServiceError("Only failed or cancelled jobs can be submitted again.")
    new, _ = create_print_job(user, job.converted_file.document, copies=job.copies, paper_size=job.paper_size,
                              color=job.color, landscape=job.landscape)
    audit("print.resubmitted", actor=user, target_type="print_job", target_id=job.public_id,
          details={"new_job": str(new.public_id)})
    return new


# ---- user actions -------------------------------------------------------------------------------------------------

def cancel_job(job: PrintJob, user) -> None:
    if job.company_id != user.company_id or not (user.is_admin or job.requested_by_id == user.id):
        abort(404)
    if job.status not in USER_CANCELLABLE:
        raise ServiceError("This job can no longer be cancelled because it has already been sent to the printer.")
    # The claim token hash is kept so the holding agent's next report gets a clear 'illegal transition', not 'unknown job'.
    _move(job, S.CANCELLED, {"finished_at": utcnow(), "lease_expires_at": None})
    audit("print.cancelled", actor=user, target_type="print_job", target_id=job.public_id)


def resolve_unknown(job: PrintJob, outcome: str, actor, note: str | None = None) -> None:
    """Administrator settles an uncertain job after looking at the printer: 'printed' or 'not_printed'."""
    if not actor.is_admin or job.company_id != actor.company_id:
        abort(404)
    if job.status != S.UNKNOWN:
        raise ServiceError("Only jobs with an uncertain outcome can be resolved.")
    if outcome == "printed":
        to, vals = S.COMPLETED, {"error_code": None, "error_message": None}
    elif outcome == "not_printed":
        to, vals = S.FAILED, {"error_code": "NOT_PRINTED_CONFIRMED", "error_message": "An administrator confirmed that nothing was printed."}
    else:
        raise ServiceError("Choose 'printed' or 'not printed'.")
    _move(job, to, {**vals, "finished_at": utcnow(), "lease_expires_at": None})
    audit("print.resolved", actor=actor, target_type="print_job", target_id=job.public_id,
          details={"outcome": outcome, "note": (note or "").strip()[:200] or None})


# ---- agent side ---------------------------------------------------------------------------------------------------

def claim_next_job(agent: Agent) -> tuple[PrintJob, str] | None:
    """Atomically take the oldest QUEUED job for this agent's office and active printers.
    Returns (job, claim_token) or None. The token is shown once; only its hash is stored. Caller commits."""
    recover_expired_leases()
    printer_ids = [p.id for p in active_printers(agent)]
    if not printer_ids:
        return None
    while True:
        jid = db.session.scalar(select(PrintJob.id).where(
            PrintJob.company_id == agent.company_id, PrintJob.office_id == agent.office_id, PrintJob.status == S.QUEUED,
            PrintJob.printer_id.in_(printer_ids)).order_by(PrintJob.id).limit(1))
        if jid is None:
            return None
        token = "sj_" + secrets.token_urlsafe(24)
        now = utcnow()
        res = db.session.execute(
            update(PrintJob).where(PrintJob.id == jid, PrintJob.status == S.QUEUED)
            .values(status=S.CLAIMED, agent_id=agent.id, claim_token_hash=_hash(token), claimed_at=now,
                    lease_expires_at=now + _lease(), error_code=None, error_message=None)
            .execution_options(synchronize_session=False))
        if res.rowcount == 1:  # otherwise another claimer won this one; look at the next
            job = db.session.get(PrintJob, jid)
            db.session.refresh(job)
            audit("print.claimed", actor_agent=agent, target_type="print_job", target_id=job.public_id)
            return job, token


def get_agent_job(agent: Agent, public_id, claim_token: str | None) -> PrintJob:
    """The job, only if this agent currently holds it and presents the right claim token. Raises JobAuthError."""
    job = db.session.scalar(select(PrintJob).where(PrintJob.public_id == public_id, PrintJob.company_id == agent.company_id,
                                                   PrintJob.agent_id == agent.id))
    if (job is None or not job.claim_token_hash or not claim_token or len(claim_token) > 200
            or not hmac.compare_digest(job.claim_token_hash, _hash(claim_token))):
        raise JobAuthError()
    return job


def agent_report(agent: Agent, job: PrintJob, to: S, *, error_code: str | None = None,
                 error_message: str | None = None) -> PrintJob:
    """Apply a status report from the agent holding `job` (authenticate first with get_agent_job). Caller commits.
    Repeating the report the server already holds is a harmless no-op (duplicate delivery)."""
    if to == job.status:
        return job
    if to not in AGENT_TARGETS:
        raise JobTransitionError("The agent may not set that status.")
    now = utcnow()
    vals: dict = {}
    if to in LEASED:
        vals["lease_expires_at"] = now + _lease()
    elif to == S.QUEUED:  # agent hands the job back before downloading (e.g. shutting down or paused)
        vals.update(agent_id=None, claim_token_hash=None, claimed_at=None, lease_expires_at=None)
    else:
        vals["lease_expires_at"] = None
    if to == S.SUBMITTED_TO_PRINTER:
        vals.update(submitted_at=now, error_code=None, error_message=None)
    if to == S.COMPLETED:
        vals.update(finished_at=now, error_code=None, error_message=None)
    if to in (S.FAILED, S.UNKNOWN):
        vals["error_code"] = (error_code or ("AGENT_REPORTED" if to == S.FAILED else "OUTCOME_UNCERTAIN"))[:64]
        vals["error_message"] = (error_message or ("The office computer could not print this job." if to == S.FAILED
                                                   else "It is not certain whether this job was printed."))[:500]
    if to == S.FAILED:
        vals["finished_at"] = now
    _move(job, to, vals)
    audit(f"print.{'released' if to == S.QUEUED else to.value.lower()}", actor_agent=agent, target_type="print_job",
          target_id=job.public_id, details={"code": vals.get("error_code")} if vals.get("error_code") else None)
    return job


def renew_lease(agent: Agent, job: PrintJob) -> PrintJob:
    """Keep a slow download/print alive without changing its status. Caller commits."""
    if job.status not in LEASED:
        raise JobTransitionError("This job is no longer being processed by the agent.")
    res = db.session.execute(update(PrintJob).where(PrintJob.id == job.id, PrintJob.status == job.status)
                             .values(lease_expires_at=utcnow() + _lease()).execution_options(synchronize_session=False))
    if res.rowcount != 1:
        db.session.refresh(job)
        raise JobTransitionError("The job was changed by someone else.")
    db.session.refresh(job)
    return job


def recover_expired_leases() -> int:
    """Handle jobs whose agent stopped reporting. Returns how many jobs were changed. Caller commits.
    CLAIMED/DOWNLOADING: nothing was printed, so requeue (until MAX_JOB_ATTEMPTS, then FAILED).
    DOWNLOADED: the print command may have run, so the outcome is UNKNOWN: never re-printed automatically."""
    now = utcnow()
    limit = current_app.config.get("MAX_JOB_ATTEMPTS", 3)
    expired = db.session.scalars(select(PrintJob).where(PrintJob.status.in_(list(LEASED)), PrintJob.lease_expires_at < now)).all()
    changed = 0
    for job in expired:
        gone = PrintJob.lease_expires_at < now  # re-checked inside the UPDATE: an agent that just renewed wins
        try:
            if job.status == S.DOWNLOADED:
                _move(job, S.UNKNOWN, {"lease_expires_at": None, "error_code": "AGENT_LOST",
                                       "error_message": "The office computer stopped responding after receiving this job. "
                                                        "Check the printer to see whether it printed."}, gone)
                audit("print.unknown", company_id=job.company_id, target_type="print_job", target_id=job.public_id,
                      details={"reason": "lease_expired"})
            else:
                requeues = db.session.scalar(select(func.count(AuditLog.id)).where(
                    AuditLog.action == "print.lease_requeued", AuditLog.target_id == str(job.public_id)))
                if requeues + 1 >= limit:
                    _move(job, S.FAILED, {"lease_expires_at": None, "finished_at": now, "error_code": "AGENT_LOST",
                                          "error_message": "The office computer repeatedly stopped responding while handling this job."}, gone)
                    audit("print.failed", company_id=job.company_id, target_type="print_job", target_id=job.public_id,
                          details={"code": "AGENT_LOST"})
                else:
                    _move(job, S.QUEUED, {"agent_id": None, "claim_token_hash": None, "claimed_at": None,
                                          "lease_expires_at": None}, gone)
                    audit("print.lease_requeued", company_id=job.company_id, target_type="print_job", target_id=job.public_id)
            changed += 1
        except JobTransitionError:
            continue  # the agent renewed, or the job moved on; leave it
    return changed
