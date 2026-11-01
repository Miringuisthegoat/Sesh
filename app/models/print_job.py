import enum

import sqlalchemy as sa
from sqlalchemy.orm import mapped_column, relationship

from ..extensions import db
from .base import PublicIdMixin, TimestampMixin, enum_type


class PrintJobStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    CLAIMED = "CLAIMED"
    DOWNLOADING = "DOWNLOADING"
    DOWNLOADED = "DOWNLOADED"
    SUBMITTED_TO_PRINTER = "SUBMITTED_TO_PRINTER"  # Windows accepted it; NOT proof that paper came out
    COMPLETED = "COMPLETED"                        # only with a reliable completion signal
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"                            # outcome uncertain: needs manual review, never auto-reprint


class PrintJob(PublicIdMixin, TimestampMixin, db.Model):
    __tablename__ = "print_jobs"
    __table_args__ = (
        sa.Index("ix_print_jobs_office_status", "office_id", "status"),
        sa.Index("ix_print_jobs_company_user_created", "company_id", "requested_by_id", "created_at"),
        sa.UniqueConstraint("requested_by_id", "idempotency_key", name="uq_print_job_user_idempotency"),
    )
    id = mapped_column(sa.Integer, primary_key=True)
    company_id = mapped_column(sa.ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False)
    office_id = mapped_column(sa.ForeignKey("offices.id", ondelete="RESTRICT"), nullable=False)
    requested_by_id = mapped_column(sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    converted_file_id = mapped_column(sa.ForeignKey("converted_files.id", ondelete="RESTRICT"), nullable=False, index=True)
    printer_id = mapped_column(sa.ForeignKey("printers.id", ondelete="RESTRICT"), nullable=False)
    agent_id = mapped_column(sa.ForeignKey("agents.id", ondelete="RESTRICT"))  # set when claimed
    status = mapped_column(enum_type(PrintJobStatus), nullable=False, default=PrintJobStatus.QUEUED)
    copies = mapped_column(sa.Integer, nullable=False, default=1)
    paper_size = mapped_column(sa.String(16), nullable=False, default="A4")
    color = mapped_column(sa.Boolean)          # NULL = printer default
    landscape = mapped_column(sa.Boolean)      # NULL = document default
    idempotency_key = mapped_column(sa.String(64))  # makes duplicate submit requests safe
    claim_token_hash = mapped_column(sa.String(64))
    claimed_at = mapped_column(sa.DateTime(timezone=True))
    lease_expires_at = mapped_column(sa.DateTime(timezone=True))
    submitted_at = mapped_column(sa.DateTime(timezone=True))
    finished_at = mapped_column(sa.DateTime(timezone=True))
    error_code = mapped_column(sa.String(64))
    error_message = mapped_column(sa.String(500))

    requested_by = relationship("User")
    converted_file = relationship("ConvertedFile")
    printer = relationship("Printer")
    agent = relationship("Agent")
