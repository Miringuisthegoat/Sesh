from flask import Blueprint, render_template
from flask_login import current_user, login_required
from sqlalchemy import func, select

from ..extensions import db
from ..models import Agent, AgentStatus, Printer, User, UserRole
from ..models import ConversionStatus, Document, DocumentStatus
from ..services.documents import visible_documents
from ..models import PrintJob, PrintJobStatus
from ..printing.labels import JOB_LABELS
from ..services.print_jobs import PENDING, visible_print_jobs
from ..services.offices import get_company_office

bp = Blueprint("dashboard", __name__)


@bp.get("/")
@login_required
def index():
    office = get_company_office(current_user.company_id)
    agent = printer = None
    if office:
        agent = db.session.scalars(select(Agent).where(Agent.office_id == office.id, Agent.status != AgentStatus.REVOKED)).first()
        printer = db.session.scalars(select(Printer).where(Printer.office_id == office.id, Printer.is_active.is_(True))).first()
    stats = None
    if current_user.is_admin:
        counts = dict(db.session.execute(
            select(User.is_active, func.count(User.id)).where(User.company_id == current_user.company_id, User.role == UserRole.WORKER)
            .group_by(User.is_active)).all())
        stats = {"active_workers": counts.get(True, 0), "inactive_workers": counts.get(False, 0)}
    visible = visible_documents(current_user)
    recent = db.session.scalars(visible.order_by(Document.created_at.desc()).limit(5)).all()
    in_progress = db.session.scalar(select(func.count()).select_from(visible.where(
        Document.status.in_([DocumentStatus.UPLOADED, DocumentStatus.QUEUED, DocumentStatus.PROCESSING])).subquery()))
    failed = db.session.scalar(select(func.count()).select_from(visible.where(Document.status == DocumentStatus.FAILED).subquery()))
    jobs = visible_print_jobs(current_user)
    recent_jobs = db.session.scalars(jobs.order_by(PrintJob.created_at.desc()).limit(5)).all()

    def _count(*states):
        return db.session.scalar(select(func.count()).select_from(jobs.where(PrintJob.status.in_(states)).subquery()))
    jobs_waiting = _count(*PENDING)
    jobs_attention = _count(PrintJobStatus.FAILED, PrintJobStatus.UNKNOWN)
    if stats is not None:
        stats["jobs_total"] = db.session.scalar(select(func.count()).select_from(jobs.subquery()))
        stats["jobs_attention"] = jobs_attention
    return render_template("dashboard.html", office=office, agent=agent, printer=printer, stats=stats,
                           recent=recent, in_progress=in_progress, failed=failed, recent_jobs=recent_jobs,
                           jobs_waiting=jobs_waiting, jobs_attention=jobs_attention, job_labels=JOB_LABELS)
