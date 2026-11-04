import io

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from app.conversion.validation import mime_acceptable
from app.extensions import db
from app.models import Document, DocumentStatus
from app.services import conversion_queue as cq
from tests.conftest import login
from tests.factories import make_docx


def make_pdf(pages=1) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i in range(pages):
        c.drawString(72, 720, f"Page {i + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


def make_encrypted_pdf() -> bytes:
    w = PdfWriter()
    w.append(PdfReader(io.BytesIO(make_pdf())))
    w.encrypt("secret")
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def upload(client, name, data, mimetype=None):
    f = (io.BytesIO(data), name, mimetype) if mimetype else (io.BytesIO(data), name)
    return client.post("/documents/upload", data={"file": f}, content_type="multipart/form-data")


@pytest.fixture()
def worker_client(client, acme):
    login(client, "worker@acme-corp.com")
    return client


def test_pdf_is_accepted_and_kept_byte_for_byte(worker_client):
    data = make_pdf(2)
    r = upload(worker_client, "report.pdf", data, "application/pdf")
    assert r.status_code == 302
    pid = r.headers["Location"].rsplit("/", 1)[-1]
    cq.run_worker(once=True)
    doc = db.session.scalar(db.select(Document))
    assert doc.source_type == "pdf" and doc.status == DocumentStatus.CONVERTED
    out = worker_client.get(f"/documents/{pid}/pdf")
    assert out.status_code == 200 and out.data == data  # printed exactly as uploaded
    assert worker_client.get(f"/documents/{pid}/pages/2.png").status_code == 200
    assert worker_client.get(f"/documents/{pid}/pages/3.png").status_code == 404


@pytest.mark.parametrize("name,data,mime,text", [
    ("a.pdf", b"just text", None, b"not a valid PDF"),
    ("b.pdf", b"", None, b"empty"),
    ("c.pdf", make_docx(), None, b"not a valid PDF"),
    ("d.pdf", make_encrypted_pdf(), None, b"password-protected"),
    ("e.pdf", b"%PDF-1.4\ngarbage", None, b"corrupt"),
    ("f.pdf", make_pdf(), "image/png", b"does not match"),
])
def test_bad_pdfs_rejected(worker_client, name, data, mime, text):
    r = upload(worker_client, name, data, mime)
    assert r.status_code == 200 and db.session.scalar(db.select(Document)) is None
    assert b"alert-danger" in r.data and text in r.data


def test_page_cap(worker_client, monkeypatch):
    monkeypatch.setattr("app.conversion.validation.MAX_PDF_PAGES", 1)
    assert upload(worker_client, "two.pdf", make_pdf(2)).status_code == 200
    assert db.session.scalar(db.select(Document)) is None
    assert upload(worker_client, "one.pdf", make_pdf(1)).status_code == 302


def test_pdf_mime_rules():
    assert mime_acceptable("pdf", "application/pdf") and mime_acceptable("pdf", "") and not mime_acceptable("pdf", "image/png")
