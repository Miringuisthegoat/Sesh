"""Tenant scoping helpers. Every lookup of a company-owned record MUST filter by company_id."""
import uuid

from flask import abort
from flask_login import current_user
from sqlalchemy import select

from ..extensions import db
from ..models import User


def get_company_user_or_404(public_id: uuid.UUID) -> User:
    user = db.session.scalar(select(User).where(User.public_id == public_id, User.company_id == current_user.company_id))
    if user is None:
        abort(404)  # 404, not 403: do not reveal that the record exists in another company
    return user
