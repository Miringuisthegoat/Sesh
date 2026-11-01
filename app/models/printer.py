import enum

import sqlalchemy as sa
from sqlalchemy.orm import mapped_column, relationship

from ..extensions import db
from .base import PublicIdMixin, TimestampMixin, enum_type


class PrinterAvailability(str, enum.Enum):
    UNKNOWN = "UNKNOWN"
    AVAILABLE = "AVAILABLE"
    OFFLINE = "OFFLINE"
    ERROR = "ERROR"


class Printer(PublicIdMixin, TimestampMixin, db.Model):
    __tablename__ = "printers"
    __table_args__ = (sa.Index("ix_printers_office_active", "office_id", "is_active"),)
    id = mapped_column(sa.Integer, primary_key=True)
    company_id = mapped_column(sa.ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True)
    office_id = mapped_column(sa.ForeignKey("offices.id", ondelete="RESTRICT"), nullable=False)
    windows_name = mapped_column(sa.String(255), nullable=False)  # exact Windows printer name
    display_name = mapped_column(sa.String(150))
    is_active = mapped_column(sa.Boolean, default=True, nullable=False)
    availability = mapped_column(enum_type(PrinterAvailability), nullable=False, default=PrinterAvailability.UNKNOWN)
    availability_checked_at = mapped_column(sa.DateTime(timezone=True))
    supports_color = mapped_column(sa.Boolean)  # NULL = unknown
    test_print_confirmed_at = mapped_column(sa.DateTime(timezone=True))

    office = relationship("Office", back_populates="printers")
