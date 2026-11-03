"""Machine API for the Sesh Windows agent. Bearer-credential auth (no cookies), JSON only, CSRF-exempt by design."""
from datetime import timezone
from functools import wraps

from flask import Response, current_app, g, jsonify, request, stream_with_context

from ..extensions import db, limiter
from ..models import PrinterAvailability, PrintJobStatus
from ..models.base import utcnow
from ..services.agents import (AgentAuthError, active_printers, authenticate, record_heartbeat, register_agent, revoke_agent,
                               rotate_credential, set_selected_printers)
from ..services.audit import audit
from ..services import print_jobs as pj
from ..services.errors import ServiceError
from ..services.storage import StorageError, get_storage
from . import bp

STATUSES = {s.value for s in PrinterAvailability}
MAX_PRINTERS = 50


def _err(code: str, message: str, status: int):
    return jsonify(error={"code": code, "message": message}), status


def _body() -> dict | None:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _parse_printers(raw) -> list[dict]:
    """Validate a list of {windows_name, status, supports_color?}. Raises ValueError with a safe message."""
    if not isinstance(raw, list) or len(raw) > MAX_PRINTERS:
        raise ValueError(f"'printers' must be a list of at most {MAX_PRINTERS} items.")
    out = []
    for p in raw:
        name = p.get("windows_name") if isinstance(p, dict) else None
        status = str(p.get("status", "UNKNOWN")).upper() if isinstance(p, dict) else ""
        color = p.get("supports_color") if isinstance(p, dict) else None
        if not isinstance(name, str) or not 1 <= len(name) <= 255 or status not in STATUSES or not (color is None or isinstance(color, bool)):
            raise ValueError("Each printer needs windows_name (1-255 chars), a valid status and optional boolean supports_color.")
        out.append({"windows_name": name, "status": status, "supports_color": color})
    return out


def agent_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        scheme, _, cred = request.headers.get("Authorization", "").partition(" ")
        agent = authenticate(cred.strip()) if scheme.lower() == "bearer" else None
        if agent is None:
            resp = _err("UNAUTHORIZED", "Invalid or revoked agent credential.", 401)
            resp[0].headers["WWW-Authenticate"] = "Bearer"
            return resp
        g.agent = agent
        return view(*args, **kwargs)
    return wrapped


def _printers_json(agent):
    return [{"id": str(p.public_id), "windows_name": p.windows_name, "display_name": p.display_name,
             "availability": p.availability.value} for p in active_printers(agent)]


@bp.post("/register")
@limiter.limit(lambda: current_app.config["AGENT_REGISTER_RATE_LIMIT"])
def register():
    data = _body()
    if data is None or not isinstance(data.get("enrollment_token"), str):
        return _err("BAD_REQUEST", "enrollment_token is required.", 400)
    try:
        agent, cred = register_agent(data["enrollment_token"], str(data.get("hostname") or ""), str(data.get("agent_version") or ""))
    except AgentAuthError:
        audit("agent.register_failed")
        db.session.commit()
        return _err("INVALID_ENROLLMENT", "The enrolment token is invalid, expired or already used.", 401)
    db.session.commit()
    return jsonify(agent_id=str(agent.public_id), credential=cred, office=agent.office.name,
                   heartbeat_seconds=current_app.config["AGENT_HEARTBEAT_SECONDS"]), 201


@bp.post("/heartbeat")
@agent_required
def heartbeat():
    data = _body() or {}
    printers = None
    if "printers" in data:
        try:
            printers = _parse_printers(data["printers"])
        except ValueError as e:
            return _err("BAD_REQUEST", str(e), 400)
    record_heartbeat(g.agent, str(data.get("agent_version") or ""), printers)
    db.session.commit()
    return jsonify(server_time=utcnow().astimezone(timezone.utc).isoformat(), heartbeat_seconds=current_app.config["AGENT_HEARTBEAT_SECONDS"],
                   printers=_printers_json(g.agent))


@bp.put("/printers")
@agent_required
def select_printers():
    data = _body()
    if data is None or not isinstance(data.get("enabled"), list) or not all(isinstance(n, str) for n in data["enabled"]):
        return _err("BAD_REQUEST", "'enabled' must be a list of Windows printer names.", 400)
    try:
        installed = _parse_printers(data.get("installed"))
        set_selected_printers(g.agent, data["enabled"], installed)
        db.session.commit()
    except ValueError as e:
        db.session.rollback()
        return _err("BAD_REQUEST", str(e), 400)
    except ServiceError as e:
        db.session.rollback()
        return _err("SELECTION_REJECTED", str(e), 409)
    return jsonify(printers=_printers_json(g.agent))


@bp.post("/credential/rotate")
@agent_required
def rotate():
    cred = rotate_credential(g.agent)
    db.session.commit()
    return jsonify(credential=cred)


@bp.post("/unregister")
@agent_required
def unregister():
    revoke_agent(g.agent)
    db.session.commit()
    return jsonify(ok=True)


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
