import pytest
import sqlalchemy as sa
from flask_migrate import check, upgrade

from app import create_app
from app.extensions import db
from app.models import Company, Office, User, UserRole
from config import TestingConfig


def test_migrations_apply_and_match_models(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    app = create_app(TestingConfig, {"SQLALCHEMY_DATABASE_URI": url})
    with app.app_context():
        upgrade(directory="migrations")
        names = set(sa.inspect(db.engine).get_table_names())
        assert {"companies", "users", "offices", "agents", "printers", "documents", "converted_files",
                "print_jobs", "agent_events", "audit_logs"} <= names
        check(directory="migrations")  # raises if models and migration drift apart


def test_public_ids_unique_and_not_sequential(app):
    c1, c2 = Company(name="A"), Company(name="B")
    db.session.add_all([c1, c2]); db.session.commit()
    assert c1.public_id != c2.public_id and len(str(c1.public_id)) == 36


def test_foreign_keys_enforced(app):
    db.session.add(Office(company_id=9999, name="Ghost"))
    with pytest.raises(sa.exc.IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_email_unique(app):
    c = Company(name="A"); db.session.add(c); db.session.flush()
    for _ in range(2):
        u = User(company_id=c.id, email="same@x.com", full_name="X", password_hash="x", role=UserRole.WORKER)
        db.session.add(u)
    with pytest.raises(sa.exc.IntegrityError):
        db.session.commit()
    db.session.rollback()
