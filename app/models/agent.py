import enum

import sqlalchemy as sa
from sqlalchemy.orm import mapped_column, relationship

from ..extensions import db
from .base import PublicIdMixin, TimestampMixin, enum_type, utcnow


class AgentStatus(str, enum.Enum):
    PENDING = "PENDING"    # created, enrolment token issued, not yet registered
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class Agent(PublicIdMixin, TimestampMixin, db.Model):
    """Machine identity for the office Windows computer. Only SHA-256 hashes of secrets are stored."""
    __tablename__ = "agents"
    __table_args__ = (sa.Index("ix_agents_office_status", "office_id", "status"),)
    id = mapped_column(sa.Integer, primary_key=True)
    company_id = mapped_column(sa.ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True)
    office_id = mapped_column(sa.ForeignKey("offices.id", ondelete="RESTRICT"), nullable=False)
    name = mapped_column(sa.String(150), nullable=False)
    status = mapped_column(enum_type(AgentStatus), nullable=False, default=AgentStatus.PENDING)
    enrollment_token_hash = mapped_column(sa.String(64), unique=True)
    enrollment_expires_at = mapped_column(sa.DateTime(timezone=True))
    credential_hash = mapped_column(sa.String(64), unique=True)
    credential_prefix = mapped_column(sa.String(12))  # non-secret hint to identify a credential in logs/UI
    hostname = mapped_column(sa.String(255))
    agent_version = mapped_column(sa.String(32))
    registered_at = mapped_column(sa.DateTime(timezone=True))
    last_seen_at = mapped_column(sa.DateTime(timezone=True))
    revoked_at = mapped_column(sa.DateTime(timezone=True))

    office = relationship("Office", back_populates="agents")
    events = relationship("AgentEvent", back_populates="agent")


class AgentEvent(db.Model):
    """Connection / health history. Agent connectivity and printer availability are tracked separately."""
    __tablename__ = "agent_events"
    __table_args__ = (sa.Index("ix_agent_events_agent_created", "agent_id", "created_at"),)
    id = mapped_column(sa.Integer, primary_key=True)
    company_id = mapped_column(sa.ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True)
    agent_id = mapped_column(sa.ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False)
    event_type = mapped_column(sa.String(40), nullable=False)  # e.g. HEARTBEAT, CONNECTED, PRINTER_STATUS
    message = mapped_column(sa.String(500))
    details = mapped_column(sa.JSON)
    created_at = mapped_column(sa.DateTime(timezone=True), default=utcnow, nullable=False)

    agent = relationship("Agent", back_populates="events")
