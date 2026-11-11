"""Worker invite links: the admin creates one, the invitee sets their own password. No password ever passes through the admin."""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from flask import current_app
from sqlalchemy import select, update

from ..extensions import db
from ..models import Company, Invite, User, UserRole
from ..models.base import utcnow
from .audit import audit
from .errors import ServiceError
from .users import create_user, normalize_email

INVALID = "This invite link is invalid, expired or already used. Ask your administrator for a new one."


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def create_invite(company: Company, *, full_name: str, email: str, actor) -> tuple[Invite, str]:
    """Returns (invite, token). The token is shown once and never stored. Earlier pending invites for the same email are revoked."""
    email = normalize_email(email)
    full_name = " ".join((full_name or "").split())
    if not full_name:
        raise ServiceError("Full name is required.")
    if db.session.scalar(select(User.id).where(User.email == email)):
        raise ServiceError("An account with this email already exists.")
    now = utcnow()
    for old in db.session.scalars(select(Invite).where(Invite.company_id == company.id, Invite.email == email,
                                                       Invite.accepted_at.is_(None), Invite.revoked_at.is_(None))):
        old.revoked_at = now
    token = "si_" + secrets.token_urlsafe(24)
    invite = Invite(company_id=company.id, created_by_id=actor.id, email=email, full_name=full_name[:150],
                    token_hash=_hash(token), expires_at=now + timedelta(hours=current_app.config["INVITE_HOURS"]))
    db.session.add(invite)
    db.session.flush()
    audit("invite.created", actor=actor, target_type="invite", target_id=invite.public_id, details={"email": email})
    return invite, token


def pending_invites(company_id: int) -> list[Invite]:
    return list(db.session.scalars(select(Invite).where(
        Invite.company_id == company_id, Invite.accepted_at.is_(None), Invite.revoked_at.is_(None),
        Invite.expires_at > utcnow()).order_by(Invite.id.desc())))


def get_valid_invite(token: str | None) -> Invite | None:
    if not token or len(token) > 200:
        return None
    inv = db.session.scalar(select(Invite).where(
        Invite.token_hash == _hash(token), Invite.accepted_at.is_(None), Invite.revoked_at.is_(None),
        Invite.expires_at > utcnow()))
    return inv if inv is not None and inv.company.is_active else None


def accept_invite(token: str, password: str) -> User:
    """Create the worker account. Caller commits; on ServiceError the caller rolls back, which also un-uses the invite."""
    inv = get_valid_invite(token)
    if inv is None:
        raise ServiceError(INVALID)
    res = db.session.execute(update(Invite).where(Invite.id == inv.id, Invite.accepted_at.is_(None))
                             .values(accepted_at=utcnow()).execution_options(synchronize_session=False))
    if res.rowcount != 1:  # a concurrent request used it first
        raise ServiceError(INVALID)
    user = create_user(inv.company, email=inv.email, full_name=inv.full_name, password=password, role=UserRole.WORKER)
    audit("invite.accepted", actor=user, target_type="invite", target_id=inv.public_id)
    return user


def revoke_invite(invite: Invite, actor) -> None:
    if invite.accepted_at is not None or invite.revoked_at is not None:
        raise ServiceError("This invite is no longer pending.")
    invite.revoked_at = utcnow()
    audit("invite.revoked", actor=actor, target_type="invite", target_id=invite.public_id)