"""Tenant scoping helpers. Every lookup of a company-owned record MUST filter by company_id."""
import uuid

from flask import abort
from flask_login import current_user
from sqlalchemy import select

from ..extensions import db
from ..models import Invite, User

def get_company_invite_or_404(public_id: uuid.UUID) -> Invite:
    inv = db.session.scalar(select(Invite).where(Invite.public_id == public_id, Invite.company_id == current_user.company_id))
    if inv is None:
        abort(404)
    return inv


def get_company_user_or_404(public_id: uuid.UUID) -> User:
    user = db.session.scalar(select(User).where(User.public_id == public_id, User.company_id == current_user.company_id))
    if user is None:
        abort(404)
    return user