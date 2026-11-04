import io

import pytest

from app.conversion.engine.errors import ConversionError
from app.conversion.validation import validate_source
from app.extensions import db
from app.models import ConvertedFile, Document, DocumentStatus
from app.services.documents import sanitize_filename
from app.services.storage import get_storage
from tests.conftest import login
from tests.factories import OLE_HEADER, make_docx, make_jpg, make_pdf, make_png, make_xlsx, make_zip_bomb


def upload(client, name, data, mimetype=None):
    return client.post("/documents/upload", data={"file": (io.BytesIO(data), name, mimetype) if mimetype else (io.BytesIO(data), name)},
                       content_type="multipart/form-data", follow_redirects=False)


@pytest.fixture()
def worker_client(client, acme):
    login(client, "worker@acme-corp.com")
    return client


@pytest.mark.parametrize("name,data", [("a.docx", make_docx()), ("b.xlsx", make_xlsx()), ("c.png", make_png()),
                                        ("d.jpg", make_jpg()), ("e.JPEG", make_jpg()), ("f.pdf", make_pdf())])
def test_supported_files_accepted_and_queued(worker_client, name, data):
    r = upload(worker_client, name, data)
    assert r.status_code == 302 and "/documents/" in r.headers["Location"]
    doc = db.session.scalar(db.select(Document))
    assert doc.status == DocumentStatus.QUEUED and doc.size_bytes == len(data)
    assert len(doc.sha256) == 64 and doc.storage_key.startswith("uploads/")
    assert get_storage().exists(doc.storage_key)
    cf = db.session.scalar(db.select(ConvertedFile))
    assert cf.attempt == 1 and cf.company_id == doc.company_id


@pytest.mark.parametrize("name,data,mime", [
    ("a.exe", b"MZ\x90\x00", None), ("b.doc", OLE_HEADER, None), ("d.pptx", make_docx(), None),
    ("noext", make_docx(), None), ("e.docx", make_png(), None), ("f.png", make_docx(), None), ("g.docx", b"just text", None),
    ("h.docx", b"", None), ("i.xlsx", make_docx(), None), ("j.docx", make_docx(), "image/png"),
])
def test_rejected_files(worker_client, name, data, mime):
    r = upload(worker_client, name, data, mime)
    assert r.status_code == 200  # form redisplayed with an error, nothing stored
    assert db.session.scalar(db.select(Document)) is None
    assert b"alert-danger" in r.data


def test_ole_container_is_reported_as_password_protected(worker_client):
    r = upload(worker_client, "locked.docx", OLE_HEADER)
    assert b"password-protected" in r.data and db.session.scalar(db.select(Document)) is None


def test_encrypted_pdf_is_rejected(worker_client):
    r = upload(worker_client, "locked.pdf", make_pdf(encrypted=True))
    assert r.status_code == 200 and db.session.scalar(db.select(Document)) is None


def test_zip_bomb_rejected(worker_client):
    r = upload(worker_client, "bomb.docx", make_zip_bomb())
    assert r.status_code == 200 and db.session.scalar(db.select(Document)) is None


def test_oversized_image_dimensions_rejected(app, tmp_path):
    p = tmp_path / "big.png"
    p.write_bytes(make_png((400, 400)))
    with pytest.raises(ConversionError) as e:
        validate_source(p, "png", max_bytes=10_000_000, max_pixels=1000)
    assert e.value.code == "IMAGE_TOO_LARGE"


def test_too_large_upload_rejected(app, client, acme):
    login(client, "worker@acme-corp.com")
    app.config["MAX_CONTENT_LENGTH"] = 100
    r = upload(client, "big.png", make_png((300, 300)))
    assert r.status_code in (200, 413) and db.session.scalar(db.select(Document)) is None


def test_no_file_rejected(worker_client):
    r = worker_client.post("/documents/upload", data={}, content_type="multipart/form-data")
    assert r.status_code == 200 and db.session.scalar(db.select(Document)) is None


def test_upload_page_explains_pdf_and_drag_drop(worker_client):
    r = worker_client.get("/documents/upload")
    assert r.status_code == 200
    assert b"Drag and drop your file here" in r.data
    assert b"PDFs are print-ready and uploaded as-is." in r.data
    assert b"Upload for printing" in r.data


def test_hostile_filename_is_sanitised_and_never_used_as_key(worker_client):
    upload(worker_client, "../../etc/pass\x00wd<script>.docx", make_docx())
    doc = db.session.scalar(db.select(Document))
    assert "/" not in doc.original_filename and "<" not in doc.original_filename and "\x00" not in doc.original_filename
    assert "etc" not in doc.storage_key and doc.storage_key.endswith(".docx")


@pytest.mark.parametrize("raw,expected", [("report.docx", "report.docx"), ("C:\\Users\\me\\Q3 report.docx", "Q3 report.docx"),
                                          ("...docx", "document.docx"), ("   .docx", "document.docx"), ("a" * 400 + ".docx", "a" * 120 + ".docx"),
                                          ("na\u202eme.docx", "name.docx")])
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw, "docx") == expected


def test_upload_requires_login(client):
    assert client.get("/documents/upload").status_code == 302
    assert client.post("/documents/upload").status_code == 302
