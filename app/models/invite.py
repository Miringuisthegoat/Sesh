import sqlalchemy as sa
from sqlalchemy.orm import mapped_column, relationship

from ..extensions import db
from .base import PublicIdMixin, TimestampMixin


class Invite(PublicIdMixin, TimestampMixin, db.Model):
    """Single-use link that lets a named person create their own worker account. Only the token's SHA-256 is stored."""
    __tablename__ = "invites"
    id = mapped_column(sa.Integer, primary_key=True)
    company_id = mapped_column(sa.ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True)
    created_by_id = mapped_column(sa.ForeignKey("users.id", ondelete="RESTRICT"))
    email = mapped_column(sa.String(254), nullable=False)
    full_name = mapped_column(sa.String(150), nullable=False)
    token_hash = mapped_column(sa.String(64), unique=True, nullable=False)
    expires_at = mapped_column(sa.DateTime(timezone=True), nullable=False)
    accepted_at = mapped_column(sa.DateTime(timezone=True))
    revoked_at = mapped_column(sa.DateTime(timezone=True))

    company = relationship("Company")