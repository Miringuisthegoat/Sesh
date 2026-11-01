import hashlib
import io
from datetime import timedelta

import pytest
from pypdf import PdfReader

from app.conversion.engine.errors import ConversionError
from app.extensions import db
from app.models import ConvertedFile, ConversionStatus, Document, DocumentStatus
from app.models.base import utcnow
from app.services import conversion_queue as cq
from app.services.documents import ingest_upload
from app.services.storage import get_storage
from tests.conftest import login
from tests.factories import make_docx, make_jpg, make_png, make_xlsx


class FakeUpload:
    def __init__(self, name, data):
        self.filename, self.mimetype, self.stream = name, "", io.BytesIO(data)


def add_doc(user, name, data):
    return ingest_upload(FakeUpload(name, data), user)


def attempts(doc):
    return db.session.scalars(db.select(ConvertedFile).where(ConvertedFile.document_id == doc.id).order_by(ConvertedFile.attempt)).all()


@pytest.mark.parametrize("name,data,min_pages", [("a.docx", make_docx(table=True), 1), ("b.xlsx", make_xlsx(), 1),
                                                  ("c.png", make_png(), 1), ("d.jpg", make_jpg(), 1)])
def test_conversion_produces_valid_stored_pdf(acme, name, data, min_pages):
    _, _, worker = acme
    doc = add_doc(worker, name, data)
    assert cq.run_worker(once=True) == 1
    db.session.refresh(doc)
    cf = attempts(doc)[-1]
    assert doc.status == DocumentStatus.CONVERTED and cf.status == ConversionStatus.SUCCEEDED
    pdf = get_storage().read_bytes(cf.storage_key)
    assert pdf.startswith(b"%PDF-") and hashlib.sha256(pdf).hexdigest() == cf.sha256 and cf.size_bytes == len(pdf)
    assert len(PdfReader(io.BytesIO(pdf)).pages) == cf.page_count >= min_pages
    assert cf.storage_key.startswith("converted/") and cf.storage_key != doc.storage_key


def test_original_is_never_overwritten(acme):
    _, _, worker = acme
    data = make_docx()
    doc = add_doc(worker, "a.docx", data)
    cq.run_worker(once=True)
    assert get_storage().read_bytes(doc.storage_key) == data


def test_empty_queue_is_a_noop(app):
    assert cq.run_worker(once=True) == 0


def test_corrupt_file_fails_without_retry(acme, monkeypatch):
    _, _, worker = acme
    doc = add_doc(worker, "a.docx", make_docx())
    # corrupt the stored object after upload validation passed (e.g. bit rot): integrity check must catch it
    st = get_storage()
    p = st._path(doc.storage_key)
    p.write_bytes(b"PK\x03\x04garbage")
    cq.run_worker(once=True)
    db.session.refresh(doc)
    assert doc.status == DocumentStatus.FAILED and doc.error_code == "STORAGE_INTEGRITY"
    assert len(attempts(doc)) == 1 and attempts(doc)[0].retryable is False


def test_conversion_error_non_retryable_not_requeued(acme, monkeypatch):
    _, _, worker = acme
    doc = add_doc(worker, "a.docx", make_docx())
    monkeypatch.setattr(cq, "convert_file", lambda *a, **k: (_ for _ in ()).throw(ConversionError("DOCX_TOO_COMPLEX", "Too complex.")))
    cq.run_worker(once=True)
    db.session.refresh(doc)
    assert doc.status == DocumentStatus.FAILED and doc.error_message == "Too complex." and len(attempts(doc)) == 1


def test_retryable_failure_retries_then_gives_up(acme, app, monkeypatch):
    _, _, worker = acme
    doc = add_doc(worker, "a.docx", make_docx())
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise RuntimeError("secret internals /srv/x")
    monkeypatch.setattr(cq, "convert_file", boom)
    assert cq.run_worker(once=True) == app.config["MAX_CONVERSION_RETRIES"]
    db.session.refresh(doc)
    assert len(calls) == 3 and [a.attempt for a in attempts(doc)] == [1, 2, 3]
    assert doc.status == DocumentStatus.FAILED and doc.error_code == "INTERNAL_ERROR"
    assert "secret" not in doc.error_message and "/srv" not in doc.error_message  # no internals leak to users
    assert not any(a.storage_key for a in attempts(doc))


def test_retry_then_success(acme, monkeypatch):
    _, _, worker = acme
    doc = add_doc(worker, "a.docx", make_docx())
    real = cq.convert_file
    n = {"i": 0}

    def flaky(*a, **k):
        n["i"] += 1
        if n["i"] == 1:
            raise RuntimeError("blip")
        return real(*a, **k)
    monkeypatch.setattr(cq, "convert_file", flaky)
    cq.run_worker(once=True)
    db.session.refresh(doc)
    assert doc.status == DocumentStatus.CONVERTED
    assert [a.status for a in attempts(doc)] == [ConversionStatus.FAILED, ConversionStatus.SUCCEEDED]


def test_claim_is_exclusive(acme):
    _, _, worker = acme
    add_doc(worker, "a.docx", make_docx())
    first, second = cq.claim_next(), cq.claim_next()
    assert first is not None and second is None


def test_claim_order_is_fifo(acme):
    _, _, worker = acme
    d1, d2 = add_doc(worker, "a.docx", make_docx()), add_doc(worker, "b.docx", make_docx())
    c1, c2 = cq.claim_next(), cq.claim_next()
    assert db.session.get(ConvertedFile, c1).document_id == d1.id and db.session.get(ConvertedFile, c2).document_id == d2.id


def test_stale_processing_attempt_is_recovered_and_retried(acme):
    _, _, worker = acme
    doc = add_doc(worker, "a.docx", make_docx())
    cid = cq.claim_next()
    db.session.get(ConvertedFile, cid).started_at = utcnow() - timedelta(hours=1)  # worker "died" an hour ago
    db.session.commit()
    assert cq.recover_stale() == 1
    db.session.refresh(doc)
    atts = attempts(doc)
    assert atts[0].status == ConversionStatus.FAILED and atts[0].error_code == "WORKER_LOST"
    assert atts[1].status == ConversionStatus.QUEUED and doc.status == DocumentStatus.QUEUED
    cq.run_worker(once=True)
    db.session.refresh(doc)
    assert doc.status == DocumentStatus.CONVERTED


def test_fresh_processing_attempt_is_not_recovered(acme):
    _, _, worker = acme
    add_doc(worker, "a.docx", make_docx())
    cq.claim_next()
    assert cq.recover_stale() == 0


def test_slow_worker_that_lost_its_lease_cannot_publish(acme, monkeypatch):
    _, _, worker = acme
    doc = add_doc(worker, "a.docx", make_docx())
    real = cq.convert_file

    def slow(*a, **k):
        res = real(*a, **k)
        cf = attempts(doc)[0]
        db.session.execute(db.update(ConvertedFile).where(ConvertedFile.id == cf.id).values(status=ConversionStatus.FAILED))
        db.session.commit()  # someone else recovered the attempt while we were converting
        return res
    monkeypatch.setattr(cq, "convert_file", slow)
    cid = cq.claim_next()
    cq.process(cid)
    st = get_storage()
    converted_dir = st.root / "converted"
    assert not any(converted_dir.glob("*.pdf")) if converted_dir.exists() else True  # output discarded
    db.session.refresh(doc)
    assert doc.status != DocumentStatus.CONVERTED


def test_deleted_document_is_not_converted(acme):
    _, _, worker = acme
    doc = add_doc(worker, "a.docx", make_docx())
    doc.deleted_at = utcnow()
    db.session.commit()
    cq.run_worker(once=True)
    cf = attempts(doc)[0]
    assert cf.status == ConversionStatus.FAILED and cf.error_code == "DOCUMENT_DELETED" and len(attempts(doc)) == 1


def test_temp_files_are_cleaned_up(acme, tmp_path, monkeypatch):
    import tempfile
    _, _, worker = acme
    add_doc(worker, "a.docx", make_docx())
    made = []
    real = tempfile.mkdtemp

    def spy(*a, **k):
        d = real(*a, **k)
        made.append(d)
        return d
    monkeypatch.setattr(cq.tempfile, "mkdtemp", spy)
    cq.run_worker(once=True)
    import os
    assert made and not any(os.path.exists(d) for d in made)


def test_audit_rows_written(acme):
    from app.models import AuditLog
    _, _, worker = acme
    add_doc(worker, "a.docx", make_docx())
    cq.run_worker(once=True)
    actions = [a.action for a in db.session.scalars(db.select(AuditLog))]
    assert "document.upload" in actions and "document.converted" in actions


def test_cli_run_worker_once(app, acme):
    _, _, worker = acme
    add_doc(worker, "a.docx", make_docx())
    r = app.test_cli_runner().invoke(args=["run-worker", "--once"])
    assert r.exit_code == 0 and "Processed 1 conversion" in r.output
