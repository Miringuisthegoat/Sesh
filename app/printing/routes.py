"""Print submission, queue, history and job actions (Phase 5c). All rules live in services/print_jobs.py."""
import uuid

from flask import abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select

from ..auth.decorators import admin_required
from ..documents.forms import CsrfOnlyForm
from ..extensions import db
from ..models import Agent, AgentStatus, DocumentStatus, PrintJob, Printer
from ..models import PrintJobStatus as S
from ..services.agents import is_online
from ..services.documents import can_modify, get_document_or_404, latest_pdf
from ..services.errors import ServiceError
from ..services.offices import get_company_office
from ..services.print_jobs import (PENDING, can_cancel, cancel_job, create_print_job, get_print_job_or_404, resolve_unknown,
                                   resubmit, visible_print_jobs)
from . import bp
from .forms import PrintOptionsForm
from .labels import JOB_HELP, JOB_LABELS

VIEWS = {
    "queue": ("Queue", list(PENDING | {S.UNKNOWN}), "Nothing is waiting to print."),
    "history": ("History", None, "No print jobs yet."),
    "attention": ("Needs attention", [S.FAILED, S.UNKNOWN], "No failed or uncertain jobs."),
}


def _office_state() -> dict:
    cid = current_user.company_id
    office = get_company_office(cid)
    agent = printer = None
    if office:
        agent = db.session.scalars(select(Agent).where(Agent.company_id == cid, Agent.office_id == office.id,
                                                       Agent.status != AgentStatus.REVOKED).order_by(Agent.id)).first()
        printer = db.session.scalars(select(Printer).where(Printer.company_id == cid, Printer.office_id == office.id,
                                                           Printer.is_active.is_(True)).order_by(Printer.id)).first()
    return {"agent": agent, "printer": printer, "can_print": bool(agent and agent.status == AgentStatus.ACTIVE and printer),
            "agent_connected": bool(agent and is_online(agent))}


def _list(view: str):
    title, states, empty = VIEWS[view]
    q = visible_print_jobs(current_user)
    if states is not None:
        q = q.where(PrintJob.status.in_(states))
    pagination = db.paginate(q.order_by(PrintJob.created_at.desc()), page=request.args.get("page", 1, type=int), per_page=25, error_out=False)
    return render_template("printing/list.html", pagination=pagination, view=view, views=VIEWS, empty=empty, labels=JOB_LABELS,
                           show_owner=current_user.is_admin)


@bp.get("/")
@login_required
def queue():
    return _list("queue")


@bp.get("/history")
@login_required
def history():
    return _list("history")


@bp.get("/attention")
@login_required
def attention():
    return _list("attention")


@bp.route("/new/<uuid:public_id>", methods=["GET", "POST"])
@login_required
def new(public_id: uuid.UUID):
    doc = get_document_or_404(public_id, current_user)
    if not can_modify(current_user, doc):
        abort(404)  # viewing another worker's document (company policy) never grants printing it
    pdf = latest_pdf(doc) if doc.status == DocumentStatus.CONVERTED else None
    if pdf is None:
        flash("This document is not ready to print yet.", "warning")
        return redirect(url_for("documents.detail", public_id=doc.public_id))
    form = PrintOptionsForm()
    if request.method == "GET":
        form.copies.data, form.paper_size.data = 1, current_user.company.default_paper_size
        form.request_id.data = uuid.uuid4().hex
    if form.validate_on_submit():
        try:
            job, created = create_print_job(
                current_user, doc, copies=form.copies.data, paper_size=form.paper_size.data,
                color={"color": True, "mono": False}.get(form.color.data), landscape={"landscape": True, "portrait": False}.get(form.orientation.data),
                idempotency_key=form.request_id.data)
            db.session.commit()
            flash("Print job queued." if created else "This print request was already submitted.", "success")
            return redirect(url_for("printing.detail", public_id=job.public_id))
        except ServiceError as e:
            db.session.rollback()
            flash(str(e), "danger")
    max_copies = min(current_user.company.max_copies, current_app.config["MAX_COPIES_LIMIT"])
    return render_template("printing/new.html", doc=doc, pdf=pdf, form=form, max_copies=max_copies, **_office_state())


@bp.get("/<uuid:public_id>")
@login_required
def detail(public_id: uuid.UUID):
    job = get_print_job_or_404(public_id, current_user)
    mine = current_user.is_admin or job.requested_by_id == current_user.id
    return render_template("printing/detail.html", job=job, doc=job.converted_file.document, labels=JOB_LABELS, explain=JOB_HELP,
                           can_cancel=can_cancel(current_user, job), can_resubmit=mine and job.status in (S.FAILED, S.CANCELLED),
                           can_resolve=current_user.is_admin and job.status == S.UNKNOWN, form=CsrfOnlyForm())


@bp.get("/<uuid:public_id>/status")
@login_required
def status(public_id: uuid.UUID):
    job = get_print_job_or_404(public_id, current_user)
    return jsonify(status=job.status.value, label=JOB_LABELS[job.status.value])


@bp.post("/<uuid:public_id>/cancel")
@login_required
def cancel(public_id: uuid.UUID):
    job = get_print_job_or_404(public_id, current_user)
    if CsrfOnlyForm().validate_on_submit():
        try:
            cancel_job(job, current_user)
            db.session.commit()
            flash("Print job cancelled.", "success")
        except ServiceError as e:
            db.session.rollback()
            flash(str(e), "danger")
    return redirect(url_for("printing.detail", public_id=job.public_id))


@bp.post("/<uuid:public_id>/resubmit")
@login_required
def resubmit_job(public_id: uuid.UUID):
    job = get_print_job_or_404(public_id, current_user)
    if CsrfOnlyForm().validate_on_submit():
        try:
            new_job = resubmit(job, current_user)
            db.session.commit()
            flash("Submitted again as a new print job.", "success")
            return redirect(url_for("printing.detail", public_id=new_job.public_id))
        except ServiceError as e:
            db.session.rollback()
            flash(str(e), "danger")
    return redirect(url_for("printing.detail", public_id=job.public_id))


@bp.post("/<uuid:public_id>/resolve")
@admin_required
def resolve(public_id: uuid.UUID):
    job = get_print_job_or_404(public_id, current_user)
    if CsrfOnlyForm().validate_on_submit():
        try:
            resolve_unknown(job, request.form.get("outcome", ""), current_user, request.form.get("note"))
            db.session.commit()
            flash("Job settled. Thank you for checking the printer.", "success")
        except ServiceError as e:
            db.session.rollback()
            flash(str(e), "danger")
    return redirect(url_for("printing.detail", public_id=job.public_id))
