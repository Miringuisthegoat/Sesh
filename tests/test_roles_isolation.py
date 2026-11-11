from sqlalchemy import select

from app.extensions import db
from app.models import User
from tests.conftest import PW, login

ADMIN_PAGES = ["/admin/workers", "/admin/company", "/admin/office", "/admin/audit"]


def test_anonymous_redirected_from_everything(client, acme):
    for url in ADMIN_PAGES:
        r = client.get(url)
        assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_worker_forbidden_on_admin_pages_and_actions(client, acme):
    _, admin, worker = acme
    login(client, "worker@acme-corp.com")
    for url in ADMIN_PAGES:
        assert client.get(url).status_code == 403
    assert client.post(f"/admin/workers/{admin.public_id}/active").status_code == 403
    assert client.post("/admin/workers", data={"full_name": "X", "email": "x@acme-corp.com", "password": PW}).status_code == 403
    assert db.session.scalar(select(User).where(User.email == "x@acme-corp.com")) is None


def test_admin_can_open_admin_pages(client, acme):
    login(client, "admin@acme-corp.com")
    for url in ADMIN_PAGES:
        assert client.get(url).status_code == 200


def test_admin_cannot_touch_other_company_users(client, acme, other):
    _, _, other_worker = other
    login(client, "admin@acme-corp.com")
    assert client.post(f"/admin/workers/{other_worker.public_id}/active").status_code == 404
    assert client.get(f"/admin/workers/{other_worker.public_id}/password").status_code == 404
    assert client.post(f"/admin/workers/{other_worker.public_id}/password", data={"password": PW + "x"}).status_code == 404
    db.session.refresh(other_worker)
    assert other_worker.is_active and other_worker.check_password(PW)


def test_worker_list_only_shows_own_company(client, acme, other):
    login(client, "admin@acme-corp.com")
    html = client.get("/admin/workers").get_data(as_text=True)
    assert "worker@acme-corp.com" in html and "worker@other-corp.com" not in html


def test_audit_log_is_company_scoped(client, acme, other):
    login(client, "admin@other-corp.com")
    client.post("/admin/workers", data={"full_name": "Zed", "email": "zed@other-corp.com", "password": PW})
    client.get("/logout")
    client.post("/logout")
    login(client, "admin@acme-corp.com")
    html = client.get("/admin/audit").get_data(as_text=True)
    assert "zed@other-corp.com" not in html
