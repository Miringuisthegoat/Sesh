import io

import pytest

from app.extensions import db
from app.models import AuditLog, Document
from app.services import conversion_queue as cq
from app.services.storage import get_storage
from tests.conftest import PW, login, make_company
from tests.factories import make_docx, make_png, make_xlsx


def up(client, name="a.docx", data=None):
    r = client.post("/documents/upload", data={"file": (io.BytesIO(data or make_docx()), name)}, content_type="multipart/form-data")
    assert r.status_code == 302, r.data
    return r.headers["Location"].rsplit("/", 1)[-1]


@pytest.fixture()
def second_worker(app, acme):
    from app.models import UserRole
    from app.services.users import create_user
    c, _, _ = acme
    w = create_user(c, email="worker2@acme-corp.com", full_name="Second Worker", password=PW, role=UserRole.WORKER)
    db.session.commit()
    return w


class _As:
    """Test client for one user. The fixture keeps one app context open for the whole test, so Flask-Login's cached
    user (flask.g) would leak between clients; clear it before every request (production has one context per request)."""

    def __init__(self, client):
        self._c = client

    def _clear(self):
        from flask import g
        g.pop("_login_user", None)

    def get(self, *a, **k):
        self._clear()
        return self._c.get(*a, **k)

    def post(self, *a, **k):
        self._clear()
        return self._c.post(*a, **k)


def client_for(app, email):
    c = app.test_client()
    _As(c)._clear()
    login(c, email)
    return _As(c)


def test_login_required_everywhere(client, acme):
    for url in ("/documents/", "/documents/upload", "/documents/00000000-0000-0000-0000-000000000000",
                "/documents/00000000-0000-0000-0000-000000000000/pdf", "/documents/00000000-0000-0000-0000-000000000000/pages/1.png",
                "/documents/00000000-0000-0000-0000-000000000000/status"):
        assert client.get(url).status_code == 302


def test_full_flow_upload_status_pdf_preview(app, client, acme):
    login(client, "worker@acme-corp.com")
    pid = up(client)
    assert client.get(f"/documents/{pid}/status").json["status"] == "QUEUED"
    assert b"updates automatically" in client.get(f"/documents/{pid}").data
    assert client.get(f"/documents/{pid}/pdf").status_code == 404  # nothing converted yet
    assert client.get(f"/documents/{pid}/pages/1.png").status_code == 404
    cq.run_worker(once=True)
    assert client.get(f"/documents/{pid}/status").json["status"] == "CONVERTED"
    r = client.get(f"/documents/{pid}/pdf")
    assert r.status_code == 200 and r.mimetype == "application/pdf" and r.data.startswith(b"%PDF-")
    assert "inline" in r.headers["Content-Disposition"] and r.headers["Cache-Control"] == "no-store"
    assert int(r.headers["Content-Length"]) == len(r.data)
    d = client.get(f"/documents/{pid}/pdf?download=1")
    assert d.headers["Content-Disposition"].startswith("attachment")
    png = client.get(f"/documents/{pid}/pages/1.png")
    assert png.status_code == 200 and png.data.startswith(b"\x89PNG")
    assert client.get(f"/documents/{pid}/pages/2.png").status_code == 404
    assert client.get(f"/documents/{pid}/pages/0.png").status_code == 404
    page = client.get(f"/documents/{pid}")
    assert b"pages/1.png" in page.data and b"Download PDF" in page.data
    assert db.session.scalar(db.select(AuditLog).where(AuditLog.action == "document.pdf_download")) is not None


def test_unicode_filename_header_is_safe(app, client, acme):
    login(client, "worker@acme-corp.com")
    pid = up(client, "Ripoti ya Mwezi – Nairobi é.docx")
    cq.run_worker(once=True)
    r = client.get(f"/documents/{pid}/pdf?download=1")
    cd = r.headers["Content-Disposition"]
    cd.encode("latin-1")  # must be a valid header value
    assert "filename*=UTF-8''" in cd


def test_worker_cannot_see_other_workers_documents(app, acme, second_worker):
    a = client_for(app, "worker@acme-corp.com")
    b = client_for(app, "worker2@acme-corp.com")
    pid = up(a)
    cq.run_worker(once=True)
    for suffix in ("", "/status", "/pdf", "/pages/1.png"):
        assert b.get(f"/documents/{pid}{suffix}").status_code == 404
    assert b.post(f"/documents/{pid}/delete").status_code == 404
    assert b"a.docx" not in b.get("/documents/").data
    assert a.get(f"/documents/{pid}/pdf").status_code == 200


def test_company_policy_lets_workers_see_all(app, acme, second_worker):
    company, _, _ = acme
    a, b = client_for(app, "worker@acme-corp.com"), client_for(app, "worker2@acme-corp.com")
    pid = up(a)
    cq.run_worker(once=True)
    company.workers_can_view_all_documents = True
    db.session.commit()
    assert b.get(f"/documents/{pid}").status_code == 200 and b.get(f"/documents/{pid}/pdf").status_code == 200
    assert b.post(f"/documents/{pid}/delete").status_code == 404  # may view, but not modify


def test_admin_sees_company_documents(app, acme):
    w, ad = client_for(app, "worker@acme-corp.com"), client_for(app, "admin@acme-corp.com")
    pid = up(w)
    assert ad.get(f"/documents/{pid}").status_code == 200
    assert b"Uploaded by" in ad.get("/documents/").data


def test_cross_company_isolation(app, acme, other):
    w = client_for(app, "worker@acme-corp.com")
    pid = up(w)
    cq.run_worker(once=True)
    for email in ("worker@other-corp.com", "admin@other-corp.com"):
        o = client_for(app, email)
        for suffix in ("", "/status", "/pdf", "/pdf?download=1", "/pages/1.png"):
            assert o.get(f"/documents/{pid}{suffix}").status_code == 404
        assert o.post(f"/documents/{pid}/delete").status_code == 404
        assert o.post(f"/documents/{pid}/retry").status_code == 404
        assert b"a.docx" not in o.get("/documents/").data


def test_unknown_id_404(app, acme):
    c = client_for(app, "worker@acme-corp.com")
    assert c.get("/documents/00000000-0000-0000-0000-000000000000").status_code == 404
    assert c.get("/documents/not-a-uuid").status_code == 404


def test_retry_only_when_failed_and_retryable(app, acme, monkeypatch):
    c = client_for(app, "worker@acme-corp.com")
    pid = up(c)
    c.post(f"/documents/{pid}/retry")  # not failed yet: refused
    assert len(db.session.scalars(db.select(Document)).one().converted_files) == 1
    monkeypatch.setattr(cq, "convert_file", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    cq.run_worker(once=True)
    doc = db.session.scalars(db.select(Document)).one()
    assert doc.status.value == "FAILED" and len(doc.converted_files) == 3
    monkeypatch.undo()
    r = c.post(f"/documents/{pid}/retry", follow_redirects=True)
    assert b"queued again" in r.data
    cq.run_worker(once=True)
    db.session.refresh(doc)
    assert doc.status.value == "CONVERTED" and len(doc.converted_files) == 4


def test_non_retryable_failure_offers_reupload_not_retry(app, acme, monkeypatch):
    from app.conversion.engine.errors import ConversionError
    c = client_for(app, "worker@acme-corp.com")
    pid = up(c)
    monkeypatch.setattr(cq, "convert_file", lambda *a, **k: (_ for _ in ()).throw(ConversionError("DOCX_MALFORMED", "Bad file.")))
    cq.run_worker(once=True)
    page = c.get(f"/documents/{pid}").data
    assert b"Bad file." in page and b"upload the file again" in page and b"Try again" not in page
    c.post(f"/documents/{pid}/retry")
    assert len(db.session.scalars(db.select(Document)).one().converted_files) == 1


def test_soft_delete_hides_but_keeps_files_and_audits(app, acme):
    c = client_for(app, "worker@acme-corp.com")
    pid = up(c)
    cq.run_worker(once=True)
    doc = db.session.scalars(db.select(Document)).one()
    key, out = doc.storage_key, doc.converted_files[0].storage_key
    assert c.post(f"/documents/{pid}/delete").status_code == 302
    assert c.get(f"/documents/{pid}").status_code == 404 and c.get(f"/documents/{pid}/pdf").status_code == 404
    assert db.session.scalars(db.select(Document)).one().deleted_at is not None
    assert get_storage().exists(key) and get_storage().exists(out)
    assert db.session.scalar(db.select(AuditLog).where(AuditLog.action == "document.delete")) is not None


def test_list_pagination_and_empty_state(app, acme):
    c = client_for(app, "worker@acme-corp.com")
    assert b"No documents yet" in c.get("/documents/").data
    up(c, "a.png", make_png())
    up(c, "b.xlsx", make_xlsx())
    body = c.get("/documents/").data
    assert b"a.png" in body and b"b.xlsx" in body and b"Waiting to convert" in body


def test_dashboard_shows_recent_documents(app, acme):
    c = client_for(app, "worker@acme-corp.com")
    up(c, "recent-one.docx")
    body = c.get("/").data
    assert b"recent-one.docx" in body and b"1 converting or waiting" in body


def test_csrf_enforced_on_upload_when_enabled(app, acme):
    app.config["WTF_CSRF_ENABLED"] = True
    c = client_for(app, "worker@acme-corp.com")
    r = c.post("/documents/upload", data={"file": (io.BytesIO(make_docx()), "a.docx")}, content_type="multipart/form-data")
    assert r.status_code == 400 and db.session.scalar(db.select(Document)) is None  # rejected, nothing stored


def test_deactivated_user_loses_access(app, acme):
    c = client_for(app, "worker@acme-corp.com")
    pid = up(c)
    _, _, worker = acme
    worker.is_active = False
    db.session.commit()
    assert c.get(f"/documents/{pid}").status_code == 302


def test_page_preview_capped(app, acme):
    app.config["MAX_PREVIEW_PAGES"] = 1
    c = client_for(app, "worker@acme-corp.com")
    pid = up(c, "long.docx", make_docx([f"Paragraph {i}" for i in range(5)] ))
    cq.run_worker(once=True)
    assert c.get(f"/documents/{pid}/pages/1.png").status_code == 200
    assert c.get(f"/documents/{pid}/pages/2.png").status_code == 404
