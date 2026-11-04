import io
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import AgentEvent, AuditLog, ConvertedFile, Document
from app.models.base import utcnow
from app.services import conversion_queue as cq
from app.services import print_jobs as pj
from app.services.documents import ingest_upload
from app.services.retention import purge_expired
from app.services.storage import StorageError, get_storage
from tests.factories import make_docx
from tests.test_printing_routes import set_up_office


class FakeUpload:
    def __init__(self, name, data):
        self.filename, self.mimetype, self.stream = name, "", io.BytesIO(data)


def converted_doc(worker, age_days=100):
    doc = ingest_upload(FakeUpload("a.docx", make_docx()), worker)
    cq.run_worker(once=True)
    db.session.refresh(doc)
    doc.created_at = utcnow() - timedelta(days=age_days)
    db.session.commit()
    return doc


def keys_of(doc):
    return [doc.storage_key] + [c.storage_key for c in doc.converted_files if c.storage_key]


def test_old_document_files_are_deleted_rows_kept_and_audited(acme):
    _, _, worker = acme
    doc = converted_doc(worker)
    keys = keys_of(doc)
    st = get_storage()
    assert len(keys) == 2 and all(st.exists(k) for k in keys)
    res = purge_expired()
    db.session.refresh(doc)
    assert res["documents"] == 1 and res["objects"] == 2 and res["errors"] == 0
    assert not any(st.exists(k) for k in keys)
    assert doc.purged_at is not None and doc.deleted_at is not None
    assert all(c.storage_key is None for c in doc.converted_files)
    assert db.session.scalar(select(Document).where(Document.id == doc.id)) is not None  # row kept
    assert "document.purged" in [a.action for a in db.session.scalars(select(AuditLog))]
    assert purge_expired()["documents"] == 0  # idempotent


def test_recent_document_is_untouched(acme):
    _, _, worker = acme
    doc = converted_doc(worker, age_days=1)
    assert purge_expired()["documents"] == 0
    assert get_storage().exists(doc.storage_key) and doc.purged_at is None


def test_company_retention_days_is_respected(acme):
    company, _, worker = acme
    doc = converted_doc(worker, age_days=10)
    company.retention_days = 5
    db.session.commit()
    assert purge_expired()["documents"] == 1


def test_dry_run_changes_nothing(acme):
    _, _, worker = acme
    doc = converted_doc(worker)
    res = purge_expired(dry_run=True)
    db.session.refresh(doc)
    assert res["documents"] == 1 and res["objects"] == 2
    assert get_storage().exists(doc.storage_key) and doc.purged_at is None and doc.deleted_at is None


def test_document_in_conversion_is_skipped(acme):
    _, _, worker = acme
    doc = ingest_upload(FakeUpload("a.docx", make_docx()), worker)  # QUEUED, worker not run
    doc.created_at = utcnow() - timedelta(days=100)
    db.session.commit()
    res = purge_expired()
    assert res["skipped"] == 1 and res["documents"] == 0 and get_storage().exists(doc.storage_key)


def test_document_with_pending_print_job_is_skipped_until_job_finishes(acme):
    company, admin, worker = acme
    set_up_office(company, admin)
    doc = converted_doc(worker)
    job, _ = pj.create_print_job(worker, doc)
    db.session.commit()
    res = purge_expired()
    assert res["skipped"] == 1 and res["documents"] == 0
    pj.cancel_job(job, worker)
    db.session.commit()
    assert purge_expired()["documents"] == 1


def test_storage_failure_leaves_document_unpurged_and_counts_error(acme, monkeypatch):
    _, _, worker = acme
    doc = converted_doc(worker)

    def boom(key):
        raise StorageError("down")
    monkeypatch.setattr(get_storage(), "delete", boom)
    res = purge_expired()
    db.session.refresh(doc)
    assert res["errors"] == 1 and res["documents"] == 0 and doc.purged_at is None
    monkeypatch.undo()
    assert purge_expired()["documents"] == 1  # a later run finishes the job


def test_old_agent_events_are_pruned(acme):
    company, admin, _ = acme
    agent = set_up_office(company, admin)
    old, new = utcnow() - timedelta(days=60), utcnow()
    db.session.add_all([AgentEvent(company_id=company.id, agent_id=agent.id, event_type="X", created_at=old),
                        AgentEvent(company_id=company.id, agent_id=agent.id, event_type="Y", created_at=new)])
    db.session.commit()
    before = db.session.scalar(select(db.func.count(AgentEvent.id)))
    assert purge_expired(dry_run=True)["agent_events"] == 1
    assert db.session.scalar(select(db.func.count(AgentEvent.id))) == before
    assert purge_expired()["agent_events"] == 1
    types = [e.event_type for e in db.session.scalars(select(AgentEvent))]
    assert "X" not in types and "Y" in types


def test_cli_purge_expired(app, acme):
    _, _, worker = acme
    converted_doc(worker)
    r = app.test_cli_runner().invoke(args=["purge-expired", "--dry-run"])
    assert r.exit_code == 0 and "Would purge 1 document" in r.output
    r = app.test_cli_runner().invoke(args=["purge-expired"])
    assert r.exit_code == 0 and "Purged 1 document" in r.output
