"""Database-backed conversion queue and worker logic.

The ConvertedFile row IS the queue entry (status QUEUED). A worker claims it with an atomic conditional UPDATE, so two
workers can never take the same attempt. A crashed worker leaves a PROCESSING row whose lease expires; recover_stale()
then fails that attempt as WORKER_LOST (retryable). A worker that was slow, not dead, cannot publish its result after
losing the lease: the final update is conditional on the attempt still being PROCESSING.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import time
from datetime import timedelta
from pathlib import Path

from flask import current_app
from sqlalchemy import select, update

from ..conversion.engine.errors import ConversionError
from ..conversion.runner import convert_file
from ..conversion.validation import sha256_file
from ..extensions import db
from ..models import ConvertedFile, ConversionStatus, Document, DocumentStatus
from ..models.base import utcnow
from .audit import audit
from .documents import queue_conversion
from .storage import StorageError, get_storage, new_key

log = logging.getLogger(__name__)


def claim_next() -> int | None:
    """Atomically move the oldest QUEUED attempt to PROCESSING. Returns its id, or None if the queue is empty."""
    while True:
        cid = db.session.scalar(select(ConvertedFile.id).where(ConvertedFile.status == ConversionStatus.QUEUED)
                                .order_by(ConvertedFile.id).limit(1))
        if cid is None:
            return None
        res = db.session.execute(update(ConvertedFile).where(ConvertedFile.id == cid, ConvertedFile.status == ConversionStatus.QUEUED)
                                 .values(status=ConversionStatus.PROCESSING, started_at=utcnow()))
        if res.rowcount == 1:
            doc_id = db.session.scalar(select(ConvertedFile.document_id).where(ConvertedFile.id == cid))
            db.session.execute(update(Document).where(Document.id == doc_id).values(status=DocumentStatus.PROCESSING))
            db.session.commit()
            return cid
        db.session.rollback()  # another worker won this one; try the next


def _fail(cid: int, code: str, message: str, retryable: bool) -> None:
    """Mark a PROCESSING attempt failed; queue a fresh attempt if retrying is allowed and attempts remain."""
    cf = db.session.get(ConvertedFile, cid)
    res = db.session.execute(update(ConvertedFile).where(ConvertedFile.id == cid, ConvertedFile.status == ConversionStatus.PROCESSING)
                             .values(status=ConversionStatus.FAILED, finished_at=utcnow(), error_code=code[:64],
                                     error_message=message[:500], retryable=retryable))
    if res.rowcount != 1:  # lease already lost; whoever recovered it owns the outcome
        db.session.rollback()
        return
    db.session.refresh(cf)
    doc = cf.document
    if retryable and cf.attempt < current_app.config["MAX_CONVERSION_RETRIES"]:
        queue_conversion(doc)
        audit("document.conversion_retry_queued", company_id=doc.company_id, target_type="document", target_id=doc.public_id,
              details={"attempt": cf.attempt, "code": code})
    else:
        doc.status = DocumentStatus.FAILED
        doc.error_code, doc.error_message = code[:64], message[:500]
        audit("document.conversion_failed", company_id=doc.company_id, target_type="document", target_id=doc.public_id,
              details={"attempt": cf.attempt, "code": code})
    db.session.commit()


def recover_stale() -> int:
    """Fail PROCESSING attempts whose lease expired (worker crashed or was killed)."""
    cutoff = utcnow() - timedelta(seconds=current_app.config["CONVERSION_LEASE_SECONDS"])
    ids = db.session.scalars(select(ConvertedFile.id).where(ConvertedFile.status == ConversionStatus.PROCESSING,
                                                            ConvertedFile.started_at < cutoff)).all()
    for cid in ids:
        log.warning("conversion attempt %s lost its worker; failing it", cid)
        _fail(cid, "WORKER_LOST", "The conversion was interrupted. It will be retried.", True)
    return len(ids)


def process(cid: int) -> None:
    """Convert one claimed attempt. Never raises for per-document problems; everything ends in a recorded state."""
    cfg = current_app.config
    cf = db.session.get(ConvertedFile, cid)
    doc = cf.document
    if doc.deleted_at is not None:
        _fail(cid, "DOCUMENT_DELETED", "The document was deleted before conversion.", False)
        return
    storage = get_storage()
    tmp = Path(tempfile.mkdtemp(prefix="sesh_conv_"))
    out_key = None
    try:
        src, dst = tmp / f"source.{doc.source_type}", tmp / "out.pdf"
        storage.download_to(doc.storage_key, src)
        if sha256_file(src) != doc.sha256:
            raise ConversionError("STORAGE_INTEGRITY", "The stored file does not match what was uploaded.")
        result = convert_file(src, doc.source_type, dst, max_bytes=cfg["MAX_CONTENT_LENGTH"], max_pixels=cfg["MAX_IMAGE_PIXELS"])
        out_key = new_key("converted", "pdf")
        storage.put_file(out_key, dst, "application/pdf")

        res = db.session.execute(update(ConvertedFile).where(ConvertedFile.id == cid, ConvertedFile.status == ConversionStatus.PROCESSING)
                                 .values(status=ConversionStatus.SUCCEEDED, finished_at=utcnow(), storage_key=out_key,
                                         sha256=result.pdf.sha256, size_bytes=result.pdf.size, page_count=result.pdf.pages,
                                         warnings=result.warnings or None, error_code=None, error_message=None, retryable=None))
        if res.rowcount != 1:  # lease lost while converting: discard our output
            db.session.rollback()
            storage.delete(out_key)
            log.warning("conversion attempt %s lost its lease; output discarded", cid)
            return
        db.session.execute(update(Document).where(Document.id == doc.id).values(status=DocumentStatus.CONVERTED,
                                                                                  error_code=None, error_message=None))
        audit("document.converted", company_id=doc.company_id, target_type="document", target_id=doc.public_id,
              details={"pages": result.pdf.pages, "warnings": len(result.warnings)})
        db.session.commit()
    except ConversionError as e:
        db.session.rollback()
        _discard(storage, out_key)
        _fail(cid, e.code, e.message, e.retryable)
    except StorageError as e:
        db.session.rollback()
        log.error("storage problem converting attempt %s: %s", cid, e)
        _fail(cid, "STORAGE_ERROR", "A temporary storage problem occurred. It will be retried.", True)
    except Exception:
        db.session.rollback()
        _discard(storage, out_key)
        log.exception("unexpected error converting attempt %s", cid)
        _fail(cid, "INTERNAL_ERROR", "The conversion failed unexpectedly.", True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _discard(storage, key) -> None:
    if key:
        try:
            storage.delete(key)
        except StorageError:
            log.warning("could not remove discarded output %s", key)


def run_once() -> bool:
    """Recover stale work, then process one queued attempt. Returns True if something was processed."""
    recover_stale()
    cid = claim_next()
    if cid is None:
        return False
    process(cid)
    return True


def run_worker(once: bool = False, poll_seconds: float | None = None) -> int:
    """Poll the queue until stopped. With once=True, drain the queue and return."""
    poll = poll_seconds if poll_seconds is not None else current_app.config["WORKER_POLL_SECONDS"]
    done = 0
    while True:
        if run_once():
            done += 1
            continue
        if once:
            return done
        time.sleep(poll)
