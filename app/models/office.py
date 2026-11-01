import sqlalchemy as sa
from sqlalchemy.orm import mapped_column, relationship

from ..extensions import db
from .base import PublicIdMixin, TimestampMixin


class Office(PublicIdMixin, TimestampMixin, db.Model):
    __tablename__ = "offices"
    __table_args__ = (sa.UniqueConstraint("company_id", "name", name="uq_office_company_name"),)
    id = mapped_column(sa.Integer, primary_key=True)
    company_id = mapped_column(sa.ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True)
    name = mapped_column(sa.String(150), nullable=False)
    location = mapped_column(sa.String(255))
    is_active = mapped_column(sa.Boolean, default=True, nullable=False)

    company = relationship("Company", back_populates="offices")
    agents = relationship("Agent", back_populates="office")
    printers = relationship("Printer", back_populates="office")
