import uuid

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from ..auth.decorators import admin_required
from ..extensions import db
from ..models import Agent, AgentStatus, AuditLog, Company, Printer, User
from ..services.agents import create_enrollment, revoke_agent
from ..services.audit import audit
from ..services.errors import ServiceError
from ..services.offices import create_office, get_company_office, update_office
from ..services.tenant import get_company_user_or_404
from ..services.users import create_user, reset_password, set_user_active
from ..models import UserRole
from .forms import CompanySettingsForm, OfficeForm, PasswordResetForm, WorkerForm

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/workers", methods=["GET", "POST"])
@admin_required
def workers():
    form = WorkerForm()
    if form.validate_on_submit():
        try:
            create_user(current_user.company, email=form.email.data, full_name=form.full_name.data,
                        password=form.password.data, role=UserRole.WORKER, actor=current_user)
            db.session.commit()
            flash("Worker account created. Share the temporary password securely and ask them to keep it private.", "success")
            return redirect(url_for("admin.workers"))
        except ServiceError as e:
            db.session.rollback()
            flash(str(e), "danger")
    users = db.session.scalars(select(User).where(User.company_id == current_user.company_id)
                               .order_by(User.role, User.full_name)).all()
    return render_template("admin/workers.html", form=form, users=users)


@bp.post("/workers/<uuid:public_id>/active")
@admin_required
def worker_set_active(public_id: uuid.UUID):
    user = get_company_user_or_404(public_id)
    want_active = not user.is_active
    try:
        set_user_active(user, want_active, current_user)
        db.session.commit()
        flash(f"{user.full_name} {'activated' if want_active else 'deactivated'}.", "success")
    except ServiceError as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(url_for("admin.workers"))


@bp.route("/workers/<uuid:public_id>/password", methods=["GET", "POST"])
@admin_required
def worker_password(public_id: uuid.UUID):
    user = get_company_user_or_404(public_id)
    form = PasswordResetForm()
    if form.validate_on_submit():
        try:
            reset_password(user, form.password.data, current_user)
            db.session.commit()
            flash(f"Password updated for {user.full_name}.", "success")
            return redirect(url_for("admin.workers"))
        except ServiceError as e:
            db.session.rollback()
            flash(str(e), "danger")
    return render_template("admin/worker_password.html", form=form, user=user)


@bp.route("/company", methods=["GET", "POST"])
@admin_required
def company_settings():
    company: Company = current_user.company
    form = CompanySettingsForm(obj=company)
    ceiling = current_app.config["MAX_COPIES_LIMIT"]
    if form.validate_on_submit():
        if form.max_copies.data > ceiling:
            form.max_copies.errors.append(f"Maximum allowed by this installation is {ceiling}.")
        else:
            before = {k: getattr(company, k) for k in ("name", "default_paper_size", "max_copies", "retention_days", "workers_can_view_all_documents")}
            form.populate_obj(company)
            company.name = " ".join(company.name.split())
            after = {k: getattr(company, k) for k in before}
            audit("company.updated", actor=current_user, target_type="company", target_id=company.public_id,
                  details={"before": before, "after": after})
            db.session.commit()
            flash("Company settings saved.", "success")
            return redirect(url_for("admin.company_settings"))
    return render_template("admin/company.html", form=form, ceiling=ceiling)


@bp.route("/office", methods=["GET", "POST"])
@admin_required
def office():
    office = get_company_office(current_user.company_id)
    form = OfficeForm(obj=office)
    if form.validate_on_submit():
        try:
            if office:
                update_office(office, name=form.name.data, location=form.location.data, actor=current_user)
            else:
                create_office(current_user.company, name=form.name.data, location=form.location.data, actor=current_user)
            db.session.commit()
            flash("Office saved.", "success")
            return redirect(url_for("admin.office"))
        except ServiceError as e:
            db.session.rollback()
            flash(str(e), "danger")
    agents = printers = []
    if office:
        agents = db.session.scalars(select(Agent).where(Agent.company_id == current_user.company_id, Agent.office_id == office.id,
                                                        Agent.status != AgentStatus.REVOKED)).all()
        printers = db.session.scalars(select(Printer).where(Printer.company_id == current_user.company_id, Printer.office_id == office.id,
                                                            Printer.is_active.is_(True))).all()
    return render_template("admin/office.html", form=form, office=office, agents=agents, printers=printers)


@bp.get("/audit")
@admin_required
def audit_log():
    from flask import request
    page = db.paginate(select(AuditLog).where(AuditLog.company_id == current_user.company_id).order_by(AuditLog.id.desc()),
                       page=request.args.get("page", 1, type=int), per_page=50, error_out=False)
    actors = {u.id: u.full_name for u in db.session.scalars(select(User).where(User.company_id == current_user.company_id))}
    return render_template("admin/audit.html", page=page, actors=actors)


@bp.post("/agent/enroll")
@admin_required
def agent_enroll():
    office = get_company_office(current_user.company_id)
    if office is None:
        flash("Set up the office first.", "danger")
        return redirect(url_for("admin.office"))
    try:
        name = " ".join((request.form.get("name") or "").split())[:150] or None
        _, token = create_enrollment(office, name=name, actor=current_user)
        db.session.commit()
    except ServiceError as e:
        db.session.rollback()
        flash(str(e), "danger")
        return redirect(url_for("admin.office"))
    agents = db.session.scalars(select(Agent).where(Agent.company_id == current_user.company_id, Agent.office_id == office.id,
                                                    Agent.status != AgentStatus.REVOKED)).all()
    printers = db.session.scalars(select(Printer).where(Printer.company_id == current_user.company_id, Printer.office_id == office.id,
                                                        Printer.is_active.is_(True))).all()
    return render_template("admin/office.html", form=OfficeForm(obj=office), office=office, agents=agents, printers=printers,
                           new_token=token, new_token_hours=current_app.config["AGENT_ENROLLMENT_HOURS"])  # shown once, never redirected


@bp.post("/agent/<uuid:public_id>/revoke")
@admin_required
def agent_revoke(public_id: uuid.UUID):
    agent = db.session.scalar(select(Agent).where(Agent.public_id == public_id, Agent.company_id == current_user.company_id))
    if agent is None:
        abort(404)
    revoke_agent(agent, current_user)
    db.session.commit()
    flash("Agent revoked.", "success")
    return redirect(url_for("admin.office"))
