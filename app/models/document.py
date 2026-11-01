import enum

import sqlalchemy as sa
from sqlalchemy.orm import mapped_column, relationship

from ..extensions import db
from .base import PublicIdMixin, TimestampMixin, enum_type


class DocumentStatus(str, enum.Enum):
    UPLOADED = "UPLOADED"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    CONVERTED = "CONVERTED"
    FAILED = "FAILED"


class Document(PublicIdMixin, TimestampMixin, db.Model):
    """An uploaded source file. The original is never overwritten."""
    __tablename__ = "documents"
    __table_args__ = (sa.Index("ix_documents_company_uploader_created", "company_id", "uploader_id", "created_at"),)
    id = mapped_column(sa.Integer, primary_key=True)
    company_id = mapped_column(sa.ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False)
    uploader_id = mapped_column(sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    original_filename = mapped_column(sa.String(255), nullable=False)  # sanitised display name only
    source_type = mapped_column(sa.String(8), nullable=False)          # docx, xlsx, jpg, jpeg, png
    mime_type = mapped_column(sa.String(100))
    size_bytes = mapped_column(sa.BigInteger, nullable=False)
    sha256 = mapped_column(sa.String(64), nullable=False)
    storage_key = mapped_column(sa.String(128), nullable=False, unique=True)  # random; never user-provided
    status = mapped_column(enum_type(DocumentStatus), nullable=False, default=DocumentStatus.UPLOADED)
    error_code = mapped_column(sa.String(64))
    error_message = mapped_column(sa.String(500))
    deleted_at = mapped_column(sa.DateTime(timezone=True))  # soft delete; retention job purges files later

    uploader = relationship("User")
    converted_files = relationship("ConvertedFile", back_populates="document", order_by="ConvertedFile.attempt")


class ConversionStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ConvertedFile(PublicIdMixin, TimestampMixin, db.Model):
    """One conversion attempt/result for a document (retries create new attempts)."""
    __tablename__ = "converted_files"
    __table_args__ = (sa.UniqueConstraint("document_id", "attempt", name="uq_converted_document_attempt"),)
    id = mapped_column(sa.Integer, primary_key=True)
    company_id = mapped_column(sa.ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True)
    document_id = mapped_column(sa.ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False)
    attempt = mapped_column(sa.Integer, nullable=False, default=1)
    status = mapped_column(enum_type(ConversionStatus), nullable=False, default=ConversionStatus.QUEUED)
    storage_key = mapped_column(sa.String(128), unique=True)  # set when a PDF exists
    sha256 = mapped_column(sa.String(64))
    size_bytes = mapped_column(sa.BigInteger)
    page_count = mapped_column(sa.Integer)
    warnings = mapped_column(sa.JSON)  # known-limitation warnings from the converter
    error_code = mapped_column(sa.String(64))
    error_message = mapped_column(sa.String(500))
    retryable = mapped_column(sa.Boolean)
    started_at = mapped_column(sa.DateTime(timezone=True))
    finished_at = mapped_column(sa.DateTime(timezone=True))

    document = relationship("Document", back_populates="converted_files")
