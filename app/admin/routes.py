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
from ..services.mailer import EmailDeliveryError, send_invite_email
from ..services.offices import create_office, get_company_office, update_office

from ..services.invites import create_invite, pending_invites, revoke_invite
from ..services.tenant import get_company_invite_or_404, get_company_user_or_404
from ..services.users import reset_password, set_user_active
from .forms import CompanySettingsForm, InviteForm, OfficeForm, PasswordResetForm


bp = Blueprint("admin", __name__, url_prefix="/admin")


def _workers_page(form, new_link=None, invited_name=None):
    users = db.session.scalars(select(User).where(User.company_id == current_user.company_id)
                               .order_by(User.role, User.full_name)).all()
    return render_template("admin/workers.html", form=form, users=users, invites=pending_invites(current_user.company_id),
                           new_link=new_link, invited_name=invited_name, invite_hours=current_app.config["INVITE_HOURS"])


@bp.route("/workers", methods=["GET", "POST"])
@admin_required
def workers():
    form = InviteForm()
    if form.validate_on_submit():
        try:
            invite, token = create_invite(current_user.company, full_name=form.full_name.data, email=form.email.data,
                                          actor=current_user)
            db.session.commit()
        except ServiceError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:  # rendered directly, never redirected: the link is shown once
            invite_url = url_for("auth.accept_invite", token=token, _external=True)
            email_sent = False
            try:
                email_sent = send_invite_email(
                    to_email=invite.email,
                    full_name=invite.full_name,
                    company_name=current_user.company.name,
                    invite_url=invite_url,
                    expires_hours=current_app.config["INVITE_HOURS"],
                )
            except EmailDeliveryError:
                current_app.logger.exception("Invite email delivery failed")
                flash("The invite was created, but email delivery failed. Copy the link below and send it manually.",
                      "warning")
            else:
                if email_sent:
                    flash(f"Invite email sent to {invite.email}.", "success")
                else:
                    flash("Email is not configured. Copy the link below and send it manually.", "warning")
            return _workers_page(InviteForm(formdata=None), None if email_sent else invite_url, invite.full_name)
    return _workers_page(form)


@bp.post("/invites/<uuid:public_id>/revoke")
@admin_required
def invite_revoke(public_id: uuid.UUID):
    invite = get_company_invite_or_404(public_id)
    try:
        revoke_invite(invite, current_user)
        db.session.commit()
        flash("Invite revoked.", "success")
    except ServiceError as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(url_for("admin.workers"))


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
