"""Office-agent enrolment, credentials, heartbeat and printer selection (Phase 4a).

Secrets (enrolment token, agent credential) are random, shown once, and stored only as SHA-256 hashes.
Agent connectivity (last_seen_at) and printer availability (Printer.availability) are tracked separately.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from flask import current_app
from sqlalchemy import select

from ..extensions import db
from ..models import Agent, AgentEvent, AgentStatus, Company, Printer, PrinterAvailability
from ..models.base import utcnow
from .audit import audit
from .errors import ServiceError
from .offices import ensure_can_add_agent, ensure_can_add_printer


class AgentAuthError(Exception):
    """Bad, expired, used or revoked enrolment token / credential. Deliberately carries no detail."""


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _event(agent: Agent, event_type: str, message: str | None = None, details: dict | None = None) -> None:
    db.session.add(AgentEvent(company_id=agent.company_id, agent_id=agent.id, event_type=event_type,
                              message=message, details=details))


def _new_credential() -> tuple[str, str, str]:
    cred = "sa_" + secrets.token_urlsafe(32)
    return cred, _hash(cred), cred[:12]


# ---- admin side -----------------------------------------------------------------------------------------------------

def create_enrollment(office, *, name: str | None, actor) -> tuple[Agent, str]:
    """Create a PENDING agent and a single-use enrolment token (returned once, never stored in clear)."""
    ensure_can_add_agent(office)
    token = "se_" + secrets.token_urlsafe(24)
    agent = Agent(company_id=office.company_id, office_id=office.id, name=(name or "Office computer")[:150],
                  status=AgentStatus.PENDING, enrollment_token_hash=_hash(token),
                  enrollment_expires_at=utcnow() + timedelta(hours=current_app.config["AGENT_ENROLLMENT_HOURS"]))
    db.session.add(agent)
    db.session.flush()
    audit("agent.enrollment_created", actor=actor, target_type="agent", target_id=agent.public_id)
    return agent, token


def revoke_agent(agent: Agent, actor=None) -> None:
    """Revoke a PENDING or ACTIVE agent. actor=None means the agent unregistered itself."""
    if agent.status == AgentStatus.REVOKED:
        return
    agent.status = AgentStatus.REVOKED
    agent.revoked_at = utcnow()
    agent.credential_hash = agent.enrollment_token_hash = agent.enrollment_expires_at = None
    _event(agent, "REVOKED", "Agent credential revoked.")
    audit("agent.revoked", actor=actor, actor_agent=None if actor else agent, company_id=agent.company_id,
          target_type="agent", target_id=agent.public_id)


# ---- agent side -----------------------------------------------------------------------------------------------------

def register_agent(enrollment_token: str, hostname: str | None, version: str | None) -> tuple[Agent, str]:
    """Exchange a valid enrolment token for a long-lived credential. Single use."""
    if not enrollment_token or len(enrollment_token) > 200:
        raise AgentAuthError()
    agent = db.session.scalar(select(Agent).where(Agent.enrollment_token_hash == _hash(enrollment_token),
                                                  Agent.status == AgentStatus.PENDING))
    if agent is None or (_aware(agent.enrollment_expires_at) or utcnow()) < utcnow():
        raise AgentAuthError()
    company = db.session.get(Company, agent.company_id)
    if not company.is_active or not agent.office.is_active:
        raise AgentAuthError()
    cred, cred_hash, prefix = _new_credential()
    now = utcnow()
    agent.credential_hash, agent.credential_prefix = cred_hash, prefix
    agent.status = AgentStatus.ACTIVE
    agent.enrollment_token_hash = agent.enrollment_expires_at = None
    agent.hostname, agent.agent_version = (hostname or "")[:255] or None, (version or "")[:32] or None
    agent.registered_at = agent.last_seen_at = now
    _event(agent, "REGISTERED", "Agent registered.", {"hostname": agent.hostname})
    audit("agent.registered", actor_agent=agent, target_type="agent", target_id=agent.public_id,
          details={"hostname": agent.hostname, "version": agent.agent_version})
    return agent, cred


def authenticate(credential: str | None) -> Agent | None:
    if not credential or len(credential) > 200:
        return None
    agent = db.session.scalar(select(Agent).where(Agent.credential_hash == _hash(credential), Agent.status == AgentStatus.ACTIVE))
    if agent is None:
        return None
    if not db.session.get(Company, agent.company_id).is_active or not agent.office.is_active:
        return None
    return agent


def rotate_credential(agent: Agent) -> str:
    """Issue a new credential; the old one stops working immediately."""
    cred, cred_hash, prefix = _new_credential()
    agent.credential_hash, agent.credential_prefix = cred_hash, prefix
    _event(agent, "CREDENTIAL_ROTATED")
    audit("agent.credential_rotated", actor_agent=agent, target_type="agent", target_id=agent.public_id)
    return cred


def is_online(agent: Agent) -> bool:
    """Agent connectivity only. Says nothing about whether the printer is usable."""
    seen = _aware(agent.last_seen_at)
    if agent.status != AgentStatus.ACTIVE or seen is None:
        return False
    return utcnow() - seen <= timedelta(seconds=current_app.config["AGENT_OFFLINE_AFTER_SECONDS"])


def active_printers(agent: Agent) -> list[Printer]:
    return list(db.session.scalars(select(Printer).where(Printer.company_id == agent.company_id, Printer.office_id == agent.office_id,
                                                         Printer.is_active.is_(True)).order_by(Printer.id)))


def _sync_status(agent: Agent, reported: list[dict]) -> None:
    by_name = {p["windows_name"]: p for p in reported}
    now = utcnow()
    for pr in active_printers(agent):
        rep = by_name.get(pr.windows_name)
        new = PrinterAvailability(rep["status"]) if rep else PrinterAvailability.OFFLINE  # removed from Windows
        if new != pr.availability:
            _event(agent, "PRINTER_STATUS", f"{pr.windows_name}: {pr.availability.value} -> {new.value}",
                   {"printer": pr.public_id.hex, "from": pr.availability.value, "to": new.value})
            pr.availability = new
        pr.availability_checked_at = now
        if rep and rep.get("supports_color") is not None:
            pr.supports_color = rep["supports_color"]


def record_heartbeat(agent: Agent, version: str | None, printers: list[dict] | None) -> None:
    was_online = is_online(agent)
    agent.last_seen_at = utcnow()
    if version:
        agent.agent_version = version[:32]
    if not was_online:
        _event(agent, "CONNECTED", "Agent connected.")
    if printers is not None:
        _sync_status(agent, printers)


def set_selected_printers(agent: Agent, enabled: list[str], installed: list[dict]) -> list[Printer]:
    """Make exactly `enabled` printers active for the agent's office. Raises ServiceError (caller rolls back)."""
    names = list(dict.fromkeys(enabled))
    known = {p["windows_name"] for p in installed}
    missing = [n for n in names if n not in known]
    if missing:
        raise ServiceError("These printers are not installed on the computer: " + ", ".join(missing))
    office = agent.office
    rows = list(db.session.scalars(select(Printer).where(Printer.company_id == agent.company_id, Printer.office_id == office.id)))
    by_name = {r.windows_name: r for r in rows}
    for r in rows:
        if r.is_active and r.windows_name not in names:
            r.is_active = False
            audit("printer.deactivated", actor_agent=agent, target_type="printer", target_id=r.public_id)
    db.session.flush()
    for n in names:
        r = by_name.get(n)
        if r is None:
            ensure_can_add_printer(office)
            r = Printer(company_id=agent.company_id, office_id=office.id, windows_name=n[:255])
            db.session.add(r)
            db.session.flush()
            audit("printer.registered", actor_agent=agent, target_type="printer", target_id=r.public_id)
        elif not r.is_active:
            ensure_can_add_printer(office)
            r.is_active = True
            db.session.flush()
            audit("printer.activated", actor_agent=agent, target_type="printer", target_id=r.public_id)
    _sync_status(agent, installed)
    return active_printers(agent)
