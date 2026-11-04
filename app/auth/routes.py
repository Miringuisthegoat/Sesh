from urllib.parse import urlsplit

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db, limiter
from ..models import User
from ..models.base import utcnow
from ..services.audit import audit
from ..services.errors import ServiceError
from ..services.users import change_own_password, company_exists, create_company_with_admin
from .forms import ChangePasswordForm, LoginForm, SetupForm

bp = Blueprint("auth", __name__)
_DUMMY_HASH = generate_password_hash("not-a-real-password")  # equalises timing for unknown emails


def _safe_next(target: str | None) -> str | None:
    if not target or "\\" in target:
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    return target


def _login_limit() -> str:
    return current_app.config["LOGIN_RATE_LIMIT"]


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit(_login_limit, methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    if not company_exists():
        return redirect(url_for("auth.setup"))
    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = db.session.scalar(select(User).where(User.email == email))
        password_ok = (user.check_password(form.password.data) if user else check_password_hash(_DUMMY_HASH, form.password.data))
        if user and password_ok and user.is_active and user.company.is_active:
            session.clear()
            login_user(user)
            session.permanent = True
            user.last_login_at = utcnow()
            audit("auth.login", actor=user, target_type="user", target_id=user.public_id)
            db.session.commit()
            return redirect(_safe_next(request.args.get("next")) or url_for("dashboard.index"))
        audit("auth.login_failed", details={"email": email[:254]})
        db.session.commit()
        flash("Invalid email or password.", "danger")  # same message for every failure: no account enumeration
    return render_template("auth/login.html", form=form)


@bp.post("/logout")
@login_required
def logout():
    audit("auth.logout", actor=current_user)
    db.session.commit()
    logout_user()
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/setup", methods=["GET", "POST"])
@limiter.limit(_login_limit, methods=["POST"])
def setup():
    """First-run only: creates the company and its first administrator. Disappears (404) once a company exists."""
    if company_exists():
        abort(404)
    form = SetupForm()
    if form.validate_on_submit():
        try:
            create_company_with_admin(company_name=form.company_name.data, admin_name=form.full_name.data,
                                      email=form.email.data, password=form.password.data)
        except ServiceError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Company created. Sign in with your new administrator account.", "success")
            return redirect(url_for("auth.login"))
    return render_template("auth/setup.html", form=form)


@bp.route("/account/password", methods=["GET", "POST"])
@login_required
@limiter.limit(_login_limit, methods=["POST"])
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        try:
            change_own_password(current_user, form.current.data, form.password.data)
            db.session.commit()
            flash("Your password was changed.", "success")
            return redirect(url_for("dashboard.index"))
        except ServiceError as e:
            db.session.rollback()
            flash(str(e), "danger")
    return render_template("auth/password.html", form=form)
