import re
from datetime import timedelta

from sqlalchemy import select

from app.extensions import db
from app.models import AuditLog, Invite, User
from app.models.base import utcnow
from app.services.invites import create_invite
from tests.conftest import login


def make_invite(company, admin, email="new@acme-corp.com", name="New Person"):
    inv, token = create_invite(company, full_name=name, email=email, actor=admin)
    db.session.commit()
    return inv, token


def test_admin_sees_link_once_and_only_hash_is_stored(client, acme):
    login(client, "admin@acme-corp.com")
    r = client.post("/admin/workers", data={"full_name": "New Person", "email": "new@acme-corp.com"})
    token = re.search(rb"/invite/(si_[A-Za-z0-9_-]{20,})", r.data).group(1).decode()
    inv = db.session.scalar(select(Invite))
    assert len(inv.token_hash) == 64 and token != inv.token_hash
    assert b"/invite/si_" not in client.get("/admin/workers").data  # not shown again


def test_admin_invite_sends_email_when_configured(client, acme, monkeypatch):
    from app.admin import routes

    sent = {}

    def fake_send_invite_email(**kwargs):
        sent.update(kwargs)
        return True

    monkeypatch.setattr(routes, "send_invite_email", fake_send_invite_email)
    login(client, "admin@acme-corp.com")
    response = client.post("/admin/workers", data={"full_name": "New Person", "email": "new@acme-corp.com"})

    assert response.status_code == 200
    assert b"Invite email sent to new@acme-corp.com" in response.data
    assert b"/invite/si_" not in response.data
    assert sent["to_email"] == "new@acme-corp.com"
    assert sent["full_name"] == "New Person"
    assert "/invite/si_" in sent["invite_url"]


def test_invite_mail_failure_keeps_manual_link(client, acme, monkeypatch):
    from app.admin import routes
    from app.services.mailer import EmailDeliveryError

    def fail_sending(**_kwargs):
        raise EmailDeliveryError("SMTP unavailable")

    monkeypatch.setattr(routes, "send_invite_email", fail_sending)
    login(client, "admin@acme-corp.com")
    response = client.post("/admin/workers", data={"full_name": "New Person", "email": "new@acme-corp.com"})

    assert response.status_code == 200
    assert b"email delivery failed" in response.data
    assert re.search(rb"/invite/si_[A-Za-z0-9_-]{20,}", response.data)


def test_invite_mailer_sends_configured_smtp_message(app, monkeypatch):
    from app.services import mailer

    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent["connection"] = (host, port, timeout)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def starttls(self, context):
            sent["tls"] = context

        def login(self, username, password):
            sent["credentials"] = (username, password)

        def send_message(self, message):
            sent["message"] = message

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    with app.app_context():
        app.config.update({
            "MAIL_SERVER": "smtp.example.test",
            "MAIL_PORT": 587,
            "MAIL_USE_TLS": True,
            "MAIL_USE_SSL": False,
            "MAIL_USERNAME": "mailer@example.test",
            "MAIL_PASSWORD": "smtp-secret",
            "MAIL_DEFAULT_SENDER": "Sesh <noreply@example.test>",
            "MAIL_TIMEOUT": 5,
        })
        assert mailer.send_invite_email(
            to_email="new@acme-corp.com",
            full_name="New Person",
            company_name="Acme",
            invite_url="https://sesh.example.test/invite/si_example",
            expires_hours=48,
        )

    message = sent["message"]
    assert sent["connection"] == ("smtp.example.test", 587, 5)
    assert sent["credentials"] == ("mailer@example.test", "smtp-secret")
    assert message["To"] == "new@acme-corp.com"
    assert "https://sesh.example.test/invite/si_example" in message.get_content()


def test_accept_invite_creates_worker_who_can_sign_in(client, acme):
    company, admin, _ = acme
    inv, token = make_invite(company, admin)
    page = client.get(f"/invite/{token}")
    assert page.status_code == 200 and b"New Person" in page.data
    r = client.post(f"/invite/{token}", data={"password": "my-own-password", "confirm": "my-own-password"})
    assert r.status_code == 302
    user = db.session.scalar(select(User).where(User.email == "new@acme-corp.com"))
    assert user.role.value == "worker" and user.company_id == company.id and user.check_password("my-own-password")
    db.session.refresh(inv)
    assert inv.accepted_at is not None
    assert login(client, "new@acme-corp.com", "my-own-password").status_code == 302
    assert {"invite.created", "invite.accepted"} <= {a.action for a in db.session.scalars(select(AuditLog))}


def test_invite_is_single_use(client, acme):
    company, admin, _ = acme
    _, token = make_invite(company, admin)
    client.post(f"/invite/{token}", data={"password": "my-own-password", "confirm": "my-own-password"})
    assert client.get(f"/invite/{token}").status_code == 404


def test_expired_and_garbage_links_are_404(client, acme):
    company, admin, _ = acme
    inv, token = make_invite(company, admin)
    inv.expires_at = utcnow() - timedelta(minutes=1)
    db.session.commit()
    assert client.get(f"/invite/{token}").status_code == 404
    assert client.get("/invite/si_nonsense").status_code == 404
    assert client.post("/invite/si_nonsense", data={"password": "x" * 12, "confirm": "x" * 12}).status_code == 404


def test_weak_or_mismatched_password_keeps_invite_usable(client, acme):
    company, admin, _ = acme
    _, token = make_invite(company, admin)
    assert b"at least 10" in client.post(f"/invite/{token}", data={"password": "short", "confirm": "short"}).data
    assert b"do not match" in client.post(f"/invite/{token}", data={"password": "long-enough-pw", "confirm": "different-pw-1"}).data
    assert db.session.scalar(select(User).where(User.email == "new@acme-corp.com")) is None
    assert client.get(f"/invite/{token}").status_code == 200


def test_existing_email_cannot_be_invited(acme):
    import pytest
    from app.services.errors import ServiceError
    company, admin, _ = acme
    with pytest.raises(ServiceError):
        create_invite(company, full_name="Dup", email="worker@acme-corp.com", actor=admin)


def test_reinvite_and_revoke_invalidate_old_links_and_are_company_scoped(client, acme, other):
    company, admin, _ = acme
    _, old_token = make_invite(company, admin)
    inv, token = make_invite(company, admin)  # same email: replaces the first link
    assert client.get(f"/invite/{old_token}").status_code == 404
    assert client.get(f"/invite/{token}").status_code == 200
    login(client, "admin@other-corp.com")
    assert client.post(f"/admin/invites/{inv.public_id}/revoke").status_code == 404
    client.post("/logout")
    login(client, "worker@acme-corp.com")
    assert client.post(f"/admin/invites/{inv.public_id}/revoke").status_code == 403
    client.post("/logout")
    login(client, "admin@acme-corp.com")
    assert client.post(f"/admin/invites/{inv.public_id}/revoke").status_code == 302
    client.post("/logout")
    assert client.get(f"/invite/{token}").status_code == 404