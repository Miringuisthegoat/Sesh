import sqlalchemy as sa
from sqlalchemy.orm import mapped_column

from ..extensions import db
from .base import utcnow


class AuditLog(db.Model):
    """Append-only record of important actions. Never store passwords, tokens or document contents here."""
    __tablename__ = "audit_logs"
    __table_args__ = (sa.Index("ix_audit_company_created", "company_id", "created_at"),)
    id = mapped_column(sa.Integer, primary_key=True)
    company_id = mapped_column(sa.ForeignKey("companies.id", ondelete="RESTRICT"))
    actor_user_id = mapped_column(sa.ForeignKey("users.id", ondelete="RESTRICT"))
    actor_agent_id = mapped_column(sa.ForeignKey("agents.id", ondelete="RESTRICT"))
    action = mapped_column(sa.String(80), nullable=False)
    target_type = mapped_column(sa.String(40))
    target_id = mapped_column(sa.String(64))
    details = mapped_column(sa.JSON)
    ip_address = mapped_column(sa.String(64))
    created_at = mapped_column(sa.DateTime(timezone=True), default=utcnow, nullable=False)
