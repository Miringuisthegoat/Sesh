import sqlalchemy as sa
from sqlalchemy.orm import mapped_column, relationship

from ..extensions import db
from .base import PublicIdMixin, TimestampMixin

PAPER_SIZES = ("A4", "A3", "Letter", "Legal")


class Company(PublicIdMixin, TimestampMixin, db.Model):
    __tablename__ = "companies"
    id = mapped_column(sa.Integer, primary_key=True)
    name = mapped_column(sa.String(200), nullable=False)
    is_active = mapped_column(sa.Boolean, default=True, nullable=False)
    default_paper_size = mapped_column(sa.String(16), default="A4", nullable=False)
    max_copies = mapped_column(sa.Integer, default=10, nullable=False)
    retention_days = mapped_column(sa.Integer, default=90, nullable=False)
    # Explicit company policy: may workers see other workers' documents? Default: no.
    workers_can_view_all_documents = mapped_column(sa.Boolean, default=False, nullable=False)

    users = relationship("User", back_populates="company")
    offices = relationship("Office", back_populates="company")
