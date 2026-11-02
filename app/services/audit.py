from flask import has_request_context, request

from ..extensions import db
from ..models import AuditLog


def audit(action: str, *, company_id: int | None = None, actor=None, actor_agent=None, target_type: str | None = None,
          target_id=None, details: dict | None = None) -> AuditLog:
    """Add an audit row to the current session (caller commits, so it is atomic with the change).
    `actor` is a human user, `actor_agent` the office agent (machine identity).
    Never pass passwords, tokens, credentials or document contents in `details`."""
    if actor is not None and company_id is None:
        company_id = actor.company_id
    if actor_agent is not None and company_id is None:
        company_id = actor_agent.company_id
    row = AuditLog(
        company_id=company_id, actor_user_id=getattr(actor, "id", None), actor_agent_id=getattr(actor_agent, "id", None),
        action=action, target_type=target_type, target_id=str(target_id) if target_id is not None else None,
        details=details or None, ip_address=request.remote_addr if has_request_context() else None,
    )
    db.session.add(row)
    return row
