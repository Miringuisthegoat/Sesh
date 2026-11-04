import pytest

from app import create_app
from app.extensions import db
from app.models import Company, UserRole
from app.services.users import create_user
from config import TestingConfig

PW = "correct-horse-battery"


@pytest.fixture()
def app(tmp_path):
    app = create_app(TestingConfig, overrides={
        "STORAGE_LOCAL_PATH": str(tmp_path / "storage"),
        "ALLOWED_EXTENSIONS": ("docx", "xlsx", "jpg", "jpeg", "png", "pdf"),
    })
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def make_company(name="Acme", admin_email="admin@acme-corp.com", worker_email="worker@acme-corp.com"):
    c = Company(name=name)
    db.session.add(c)
    db.session.flush()
    admin = create_user(c, email=admin_email, full_name=f"{name} Admin", password=PW, role=UserRole.ADMIN)
    worker = create_user(c, email=worker_email, full_name=f"{name} Worker", password=PW, role=UserRole.WORKER)
    db.session.commit()
    return c, admin, worker


@pytest.fixture()
def acme(app):
    return make_company()


@pytest.fixture()
def other(app):
    return make_company("Other", "admin@other-corp.com", "worker@other-corp.com")


def login(client, email, password=PW):
    return client.post("/login", data={"email": email, "password": password}, follow_redirects=False)
