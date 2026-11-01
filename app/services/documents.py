"""Upload ingestion, access rules and lifecycle for documents. All lookups are tenant-scoped."""
from __future__ import annotations

import hashlib
import logging
import re
import tempfile
import unicodedata
import uuid
from pathlib import Path

from flask import abort, current_app
from sqlalchemy import select

from ..conversion.engine.errors import ConversionError
from ..conversion.validation import mime_acceptable, validate_source
from ..extensions import db
from ..models import ConvertedFile, ConversionStatus, Document, DocumentStatus
from .audit import audit
from .errors import ServiceError
from .storage import StorageError, get_storage, new_key

log = logging.getLogger(__name__)

_BAD_CHARS = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')


def sanitize_filename(raw: str | None, ext: str) -> str:
    """Display name only. Strips directories, control/reserved characters and bidi tricks; never used as a path."""
    name = (raw or "").replace("\\", "/").split("/")[-1]
    name = unicodedata.normalize("NFC", name)
    name = "".join(c for c in name if unicodedata.category(c) not in ("Cc", "Cf"))
    name = _BAD_CHARS.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip()
    if name.lower().endswith("." + ext):
        name = name[: -(len(ext) + 1)]
    stem = name.strip(" .")
    if not stem or set(stem) <= {"_"}:
        stem = "document"
    return f"{stem[:120]}.{ext}"


def _extension(filename: str | None) -> str:
    ext = Path((filename or "").replace("\\", "/").split("/")[-1]).suffix.lower().lstrip(".")
    allowed = current_app.config["ALLOWED_EXTENSIONS"]
    if not ext or ext not in allowed:
        raise ServiceError("This file type is not supported. Upload a .docx, .xlsx, .jpg, .jpeg or .png file.")
    return ext


# ---- access rules -------------------------------------------------------------------------------------------------

def visible_documents(user):
    """Documents this user may see. Admins: whole company. Workers: their own, or the company's if policy allows."""
    q = select(Document).where(Document.company_id == user.company_id, Document.deleted_at.is_(None))
    if not user.is_admin and not user.company.workers_can_view_all_documents:
        q = q.where(Document.uploader_id == user.id)
    return q


def get_document_or_404(public_id: uuid.UUID, user) -> Document:
    doc = db.session.scalar(visible_documents(user).where(Document.public_id == public_id))
    if doc is None:
        abort(404)  # same answer for "missing", "other company" and "not yours"
    return doc


def can_modify(user, doc: Document) -> bool:
    return user.is_admin or doc.uploader_id == user.id


def latest_pdf(doc: Document) -> ConvertedFile | None:
    return db.session.scalar(select(ConvertedFile).where(
        ConvertedFile.document_id == doc.id, ConvertedFile.company_id == doc.company_id,
        ConvertedFile.status == ConversionStatus.SUCCEEDED).order_by(ConvertedFile.attempt.desc()))


def latest_attempt(doc: Document) -> ConvertedFile | None:
    return db.session.scalar(select(ConvertedFile).where(ConvertedFile.document_id == doc.id)
                             .order_by(ConvertedFile.attempt.desc()))


# ---- lifecycle ----------------------------------------------------------------------------------------------------

def queue_conversion(doc: Document) -> ConvertedFile:
    """Create the next conversion attempt (QUEUED) for a document. Caller commits."""
    last = latest_attempt(doc)
    cf = ConvertedFile(company_id=doc.company_id, document_id=doc.id, attempt=(last.attempt + 1) if last else 1,
                       status=ConversionStatus.QUEUED)
    db.session.add(cf)
    doc.status = DocumentStatus.QUEUED
    doc.error_code = doc.error_message = None
    return cf


def ingest_upload(file_storage, user) -> Document:
    """Validate and store an uploaded file, then queue it for conversion. Raises ServiceError with a safe message."""
    cfg = current_app.config
    if file_storage is None or not file_storage.filename:
        raise ServiceError("Choose a file to upload.")
    ext = _extension(file_storage.filename)
    if not mime_acceptable(ext, file_storage.mimetype):
        raise ServiceError("The file type reported by your browser does not match its extension.")
    limit = cfg["MAX_CONTENT_LENGTH"]

    with tempfile.TemporaryDirectory(prefix="sesh_up_") as tmp:
        path = Path(tmp) / f"upload.{ext}"
        h, size = hashlib.sha256(), 0
        with open(path, "wb") as out:
            while chunk := file_storage.stream.read(1024 * 256):
                size += len(chunk)
                if size > limit:
                    raise ServiceError(f"The file is larger than the {limit // (1024 * 1024)} MB limit.")
                h.update(chunk)
                out.write(chunk)
        try:
            info = validate_source(path, ext, max_bytes=limit, max_pixels=cfg["MAX_IMAGE_PIXELS"])
        except ConversionError as e:
            raise ServiceError(e.message) from e

        storage = get_storage()
        key = new_key("uploads", ext)
        try:
            storage.put_file(key, path, info.mime)
        except StorageError as e:
            log.error("storage write failed during upload: %s", e)
            raise ServiceError("The file could not be saved right now. Please try again.") from e

    try:
        doc = Document(company_id=user.company_id, uploader_id=user.id,
                       original_filename=sanitize_filename(file_storage.filename, ext), source_type=ext, mime_type=info.mime,
                       size_bytes=size, sha256=h.hexdigest(), storage_key=key, status=DocumentStatus.UPLOADED)
        db.session.add(doc)
        db.session.flush()
        queue_conversion(doc)
        audit("document.upload", actor=user, target_type="document", target_id=doc.public_id,
              details={"type": ext, "size_bytes": size})
        db.session.commit()
    except Exception:
        db.session.rollback()
        try:
            storage.delete(key)  # do not leave an orphaned object behind
        except StorageError:
            log.warning("could not remove orphaned upload %s", key)
        raise
    return doc


def request_retry(doc: Document, user) -> None:
    if not can_modify(user, doc):
        abort(404)
    last = latest_attempt(doc)
    if doc.status != DocumentStatus.FAILED or last is None or not last.retryable:
        raise ServiceError("This document cannot be retried. Fix the problem described and upload the file again.")
    queue_conversion(doc)
    audit("document.retry", actor=user, target_type="document", target_id=doc.public_id)


def soft_delete(doc: Document, user) -> None:
    """Hide the document. Stored files are kept until the retention policy purges them (later phase)."""
    if not can_modify(user, doc):
        abort(404)
    from ..models.base import utcnow
    doc.deleted_at = utcnow()
    audit("document.delete", actor=user, target_type="document", target_id=doc.public_id)
