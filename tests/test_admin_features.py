import pytest
from sqlalchemy import select

from app import create_app
from app.extensions import db
from app.models import AuditLog, Office, UserRole
from app.services.errors import ServiceError
from app.services.offices import create_office, ensure_can_add_agent, ensure_can_add_printer
from config import TestingConfig
from tests.conftest import PW, login, make_company


def actions():
    return [a.action for a in db.session.scalars(select(AuditLog).order_by(AuditLog.id))]


def test_create_worker_and_duplicate_email_rejected(client, acme):
    login(client, "admin@acme-corp.com")
    data = {"full_name": "New Person", "email": "new@acme-corp.com", "password": "long-enough-pw"}
    assert client.post("/admin/workers", data=data).status_code == 302
    r = client.post("/admin/workers", data=data)
    assert b"already exists" in r.data
    assert "user.created" in actions()


def test_weak_password_rejected(client, acme):
    login(client, "admin@acme-corp.com")
    r = client.post("/admin/workers", data={"full_name": "A", "email": "weak@acme-corp.com", "password": "short"})
    assert b"at least 10" in r.data


def test_deactivate_and_reactivate_worker(client, acme):
    _, _, worker = acme
    login(client, "admin@acme-corp.com")
    client.post(f"/admin/workers/{worker.public_id}/active")
    db.session.refresh(worker)
    assert worker.is_active is False
    client.post(f"/admin/workers/{worker.public_id}/active")
    db.session.refresh(worker)
    assert worker.is_active is True
    assert {"user.deactivated", "user.activated"} <= set(actions())


def test_admin_cannot_deactivate_self_or_last_admin(client, acme):
    _, admin, _ = acme
    login(client, "admin@acme-corp.com")
    r = client.post(f"/admin/workers/{admin.public_id}/active", follow_redirects=True)
    assert b"cannot deactivate your own" in r.data
    db.session.refresh(admin)
    assert admin.is_active


def test_cannot_remove_last_active_admin(app):
    from app.services.users import create_user, set_user_active
    c, a1, _ = make_company()
    a2 = create_user(c, email="a2@acme-corp.com", full_name="Two", password=PW, role=UserRole.ADMIN)
    set_user_active(a2, False, a1)  # fine: a1 remains
    with pytest.raises(ServiceError):
        set_user_active(a1, False, a2)  # a2 (inactive) acting; a1 is the last active admin


def test_password_reset_changes_password_and_never_logs_it(client, acme):
    _, _, worker = acme
    login(client, "admin@acme-corp.com")
    client.post(f"/admin/workers/{worker.public_id}/password", data={"password": "brand-new-password"})
    db.session.refresh(worker)
    assert worker.check_password("brand-new-password")
    blob = " ".join(str(a.details) for a in db.session.scalars(select(AuditLog)))
    assert "brand-new-password" not in blob and PW not in blob


def test_company_settings_update_and_ceiling(client, acme):
    company, _, _ = acme
    login(client, "admin@acme-corp.com")
    ok = {"name": "Acme Ltd", "default_paper_size": "Letter", "max_copies": 5, "retention_days": 30, "workers_can_view_all_documents": "y"}
    assert client.post("/admin/company", data=ok).status_code == 302
    db.session.refresh(company)
    assert (company.name, company.default_paper_size, company.max_copies, company.retention_days, company.workers_can_view_all_documents) == ("Acme Ltd", "Letter", 5, 30, True)
    bad = dict(ok, max_copies=999)
    assert b"Maximum allowed" in client.post("/admin/company", data=bad).data
    assert "company.updated" in actions()


def test_office_one_per_company_enforced_and_configurable(client, acme):
    company, admin, _ = acme
    login(client, "admin@acme-corp.com")
    assert client.post("/admin/office", data={"name": "HQ", "location": "Nairobi"}).status_code == 302
    assert db.session.scalar(select(Office).where(Office.name == "HQ")) is not None
    # second create attempt through the service is refused under the MVP limit
    with pytest.raises(ServiceError):
        create_office(company, name="Branch", location=None, actor=admin)
    # a later multi-office release only needs a config change
    app = create_app(TestingConfig, {"MAX_OFFICES_PER_COMPANY": 2})
    with app.app_context():
        db.create_all()
        c, a, _ = make_company("Multi", "a@multi-corp.com", "w@multi-corp.com")
        create_office(c, name="One", location=None, actor=a)
        create_office(c, name="Two", location=None, actor=a)
        db.session.commit()


def test_office_update_via_post_renames(client, acme):
    login(client, "admin@acme-corp.com")
    client.post("/admin/office", data={"name": "HQ"})
    client.post("/admin/office", data={"name": "Head Office", "location": "Westlands"})
    assert db.session.scalar(select(Office.name)) == "Head Office"
    assert db.session.scalar(select(Office).where(Office.name == "HQ")) is None


def test_agent_and_printer_slot_limits(app, acme):
    from app.models import Agent, AgentStatus, Printer
    company, admin, _ = acme
    office = create_office(company, name="HQ", location=None, actor=admin)
    db.session.flush()
    ensure_can_add_agent(office); ensure_can_add_printer(office)
    db.session.add_all([Agent(company_id=company.id, office_id=office.id, name="PC", status=AgentStatus.PENDING),
                        Printer(company_id=company.id, office_id=office.id, windows_name="HP")])
    db.session.commit()
    with pytest.raises(ServiceError):
        ensure_can_add_agent(office)
    with pytest.raises(ServiceError):
        ensure_can_add_printer(office)
    # revoked agent frees the slot
    agent = db.session.scalar(select(Agent)); agent.status = AgentStatus.REVOKED; db.session.commit()
    ensure_can_add_agent(office)


def test_login_and_failed_login_are_audited(client, acme):
    client.post("/login", data={"email": "admin@acme-corp.com", "password": "wrong-wrong-wrong"})
    login(client, "admin@acme-corp.com")
    assert {"auth.login_failed", "auth.login"} <= set(actions())
    assert PW not in " ".join(str(a.details) for a in db.session.scalars(select(AuditLog)))
