"""Retention purge (Phase 7): delete stored files of documents older than the company's retention period.

Database rows are KEPT (print history and audit trail stay intact); only the stored objects go. A purged document is
hidden (deleted_at) and marked purged_at, and its converted files lose their storage_key. Documents that are still
in use are skipped: conversion in flight, or a print job that is waiting, held by the agent, or of uncertain outcome.
Idempotent: running it twice, or after a partial storage failure, is safe (deleting a missing object is a no-op).
Also prunes old AgentEvent rows. Audit logs are never pruned.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from flask import current_app
from sqlalchemy import delete, func, select

from ..extensions import db
from ..models import AgentEvent, Company, ConvertedFile, Document, DocumentStatus, PrintJob
from ..models import PrintJobStatus as S
from ..models.base import utcnow
from .audit import audit
from .print_jobs import PENDING
from .storage import StorageError, get_storage

log = logging.getLogger(__name__)
BLOCKING_JOB_STATES = list(PENDING | {S.UNKNOWN})
IN_FLIGHT_DOC_STATES = (DocumentStatus.UPLOADED, DocumentStatus.QUEUED, DocumentStatus.PROCESSING)


def _in_use(doc: Document) -> bool:
    if doc.status in IN_FLIGHT_DOC_STATES:
        return True
    n = db.session.scalar(
        select(func.count(PrintJob.id)).join(ConvertedFile, PrintJob.converted_file_id == ConvertedFile.id)
        .where(ConvertedFile.document_id == doc.id, PrintJob.status.in_(BLOCKING_JOB_STATES)))
    return bool(n)


def purge_expired(*, dry_run: bool = False, now=None) -> dict:
    """Returns counts: documents, objects, skipped, errors, agent_events. With dry_run nothing is changed."""
    now = now or utcnow()
    out = {"documents": 0, "objects": 0, "skipped": 0, "errors": 0, "agent_events": 0}
    storage = get_storage()
    for company in db.session.scalars(select(Company).order_by(Company.id)).all():
        cutoff = now - timedelta(days=company.retention_days)
        docs = db.session.scalars(select(Document).where(
            Document.company_id == company.id, Document.purged_at.is_(None), Document.created_at < cutoff)
            .order_by(Document.id)).all()
        for doc in docs:
            if _in_use(doc):
                out["skipped"] += 1
                continue
            files = [c for c in doc.converted_files if c.storage_key]
            keys = [doc.storage_key] + [c.storage_key for c in files]
            if dry_run:
                out["documents"] += 1
                out["objects"] += len(keys)
                continue
            try:
                for key in keys:
                    storage.delete(key)
            except StorageError as e:
                db.session.rollback()
                out["errors"] += 1
                log.error("retention: could not delete files of document %s: %s", doc.public_id, e)
                continue
            for c in files:
                c.storage_key = None
            doc.purged_at = now
            if doc.deleted_at is None:
                doc.deleted_at = now
            audit("document.purged", company_id=company.id, target_type="document", target_id=doc.public_id,
                  details={"objects": len(keys)})
            db.session.commit()
            out["documents"] += 1
            out["objects"] += len(keys)
    days = current_app.config.get("AGENT_EVENT_RETENTION_DAYS", 30)
    ecut = now - timedelta(days=days)
    if dry_run:
        out["agent_events"] = db.session.scalar(select(func.count(AgentEvent.id)).where(AgentEvent.created_at < ecut)) or 0
    else:
        out["agent_events"] = db.session.execute(delete(AgentEvent).where(AgentEvent.created_at < ecut)).rowcount or 0
        db.session.commit()
    return out
