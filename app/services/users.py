from email_validator import EmailNotValidError, validate_email
from flask import current_app
from sqlalchemy import func, select

from ..extensions import db
from ..models import Company, Office, User, UserRole
from .audit import audit
from .errors import ServiceError


def normalize_email(raw: str) -> str:
    try:
        return validate_email((raw or "").strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError as e:
        raise ServiceError(f"Invalid email address: {e}") from e


def validate_password(password: str, email: str = "") -> None:
    minimum = current_app.config["PASSWORD_MIN_LENGTH"]
    if len(password or "") < minimum:
        raise ServiceError(f"Password must be at least {minimum} characters.")
    if len(password) > 128:
        raise ServiceError("Password must be at most 128 characters.")
    if password.lower() == (email or "").lower() or password.isdigit():
        raise ServiceError("Password is too easy to guess.")


def create_user(company: Company, *, email: str, full_name: str, password: str, role: UserRole, actor=None) -> User:
    email = normalize_email(email)
    full_name = " ".join((full_name or "").split())
    if not full_name:
        raise ServiceError("Full name is required.")
    validate_password(password, email)
    if db.session.scalar(select(User.id).where(User.email == email)):
        raise ServiceError("An account with this email already exists.")
    user = User(company_id=company.id, email=email, full_name=full_name[:150], role=role, is_active=True)
    user.set_password(password)
    db.session.add(user)
    db.session.flush()
    audit("user.created", company_id=company.id, actor=actor, target_type="user", target_id=user.public_id,
          details={"role": role.value, "email": email})
    return user


def set_user_active(user: User, active: bool, actor) -> None:
    if user.id == actor.id and not active:
        raise ServiceError("You cannot deactivate your own account.")
    if user.is_active == active:
        return
    if not active and user.role == UserRole.ADMIN:
        remaining = db.session.scalar(select(func.count(User.id)).where(
            User.company_id == user.company_id, User.role == UserRole.ADMIN, User.is_active.is_(True), User.id != user.id))
        if not remaining:
            raise ServiceError("At least one active administrator is required.")
    user.is_active = active
    audit("user.activated" if active else "user.deactivated", actor=actor, target_type="user", target_id=user.public_id)


def reset_password(user: User, new_password: str, actor) -> None:
    validate_password(new_password, user.email)
    user.set_password(new_password)
    audit("user.password_reset", actor=actor, target_type="user", target_id=user.public_id)  # never log the password


def create_company_with_admin(*, company_name: str, admin_name: str, email: str, password: str) -> tuple[Company, User]:
    company_name = " ".join((company_name or "").split())
    if not company_name:
        raise ServiceError("Company name is required.")
    company = Company(name=company_name[:200], retention_days=current_app.config["RETENTION_DAYS_DEFAULT"])
    db.session.add(company)
    db.session.flush()
    audit("company.created", company_id=company.id, target_type="company", target_id=company.public_id)
    admin = create_user(company, email=email, full_name=admin_name, password=password, role=UserRole.ADMIN)
    db.session.commit()
    return company, admin


def company_exists() -> bool:
    return db.session.scalar(select(func.count(Company.id))) > 0
