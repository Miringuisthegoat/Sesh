from flask import current_app
from sqlalchemy import func, select

from ..extensions import db
from ..models import Agent, AgentStatus, Company, Office, Printer
from .audit import audit
from .errors import ServiceError


def get_company_office(company_id: int) -> Office | None:
    """MVP: a company has at most one active office; returns it (or None)."""
    return db.session.scalars(select(Office).where(Office.company_id == company_id, Office.is_active.is_(True))
                              .order_by(Office.id)).first()


def create_office(company: Company, *, name: str, location: str | None, actor) -> Office:
    name = " ".join((name or "").split())
    if not name:
        raise ServiceError("Office name is required.")
    active = db.session.scalar(select(func.count(Office.id)).where(Office.company_id == company.id, Office.is_active.is_(True)))
    limit = current_app.config["MAX_OFFICES_PER_COMPANY"]
    if active >= limit:
        raise ServiceError(f"This version supports {limit} office per company.")
    if db.session.scalar(select(Office.id).where(Office.company_id == company.id, Office.name == name)):
        raise ServiceError("An office with this name already exists.")
    office = Office(company_id=company.id, name=name[:150], location=(location or "").strip()[:255] or None)
    db.session.add(office)
    db.session.flush()
    audit("office.created", actor=actor, target_type="office", target_id=office.public_id, details={"name": office.name})
    return office


def update_office(office: Office, *, name: str, location: str | None, actor) -> None:
    name = " ".join((name or "").split())
    if not name:
        raise ServiceError("Office name is required.")
    clash = db.session.scalar(select(Office.id).where(Office.company_id == office.company_id, Office.name == name, Office.id != office.id))
    if clash:
        raise ServiceError("An office with this name already exists.")
    before = {"name": office.name, "location": office.location}
    office.name, office.location = name[:150], (location or "").strip()[:255] or None
    audit("office.updated", actor=actor, target_type="office", target_id=office.public_id,
          details={"before": before, "after": {"name": office.name, "location": office.location}})


def ensure_can_add_agent(office: Office) -> None:
    """Used by agent enrolment (Phase 4). Pending and active agents both occupy a slot."""
    count = db.session.scalar(select(func.count(Agent.id)).where(
        Agent.office_id == office.id, Agent.status.in_([AgentStatus.PENDING, AgentStatus.ACTIVE])))
    limit = current_app.config["MAX_ACTIVE_AGENTS_PER_OFFICE"]
    if count >= limit:
        raise ServiceError(f"This office already has {limit} registered agent. Revoke it before adding another.")


def ensure_can_add_printer(office: Office) -> None:
    """Used by printer registration (Phase 4)."""
    count = db.session.scalar(select(func.count(Printer.id)).where(Printer.office_id == office.id, Printer.is_active.is_(True)))
    limit = current_app.config["MAX_ACTIVE_PRINTERS_PER_OFFICE"]
    if count >= limit:
        raise ServiceError(f"This office already has {limit} active printer. Deactivate it before adding another.")
