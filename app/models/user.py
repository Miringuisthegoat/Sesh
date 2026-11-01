import enum

import sqlalchemy as sa
from flask_login import UserMixin
from sqlalchemy.orm import mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db, login_manager
from .base import PublicIdMixin, TimestampMixin, enum_type


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    WORKER = "worker"


class User(UserMixin, PublicIdMixin, TimestampMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (sa.Index("ix_users_company_role_active", "company_id", "role", "is_active"),)
    id = mapped_column(sa.Integer, primary_key=True)
    company_id = mapped_column(sa.ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True)
    email = mapped_column(sa.String(254), nullable=False, unique=True)  # always stored lowercase
    full_name = mapped_column(sa.String(150), nullable=False)
    password_hash = mapped_column(sa.String(255), nullable=False)
    role = mapped_column(enum_type(UserRole), nullable=False, default=UserRole.WORKER)
    is_active = mapped_column(sa.Boolean, default=True, nullable=False)
    last_login_at = mapped_column(sa.DateTime(timezone=True))

    company = relationship("Company", back_populates="users")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)  # scrypt by default in current Werkzeug

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN


@login_manager.user_loader
def load_user(user_id: str):
    """Runs on every request, so deactivating a user or company takes effect immediately."""
    try:
        user = db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None
    if user is None or not user.is_active or not user.company.is_active:
        return None
    return user
