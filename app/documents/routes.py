import uuid
from urllib.parse import quote

from flask import Response, abort, current_app, flash, jsonify, redirect, render_template, request, stream_with_context, url_for
from flask_login import current_user, login_required

from ..extensions import db, limiter
from ..models import Document, DocumentStatus
from ..services.audit import audit
from ..services.documents import (can_modify, get_document_or_404, ingest_upload, latest_attempt, latest_pdf, request_retry,
                                  soft_delete, visible_documents)
from ..services.errors import ServiceError
from ..services.preview import render_page_png
from ..services.storage import StorageError, get_storage
from . import bp
from .forms import CsrfOnlyForm

STATUS_LABELS = {"UPLOADED": "Uploaded", "QUEUED": "Waiting to prepare", "PROCESSING": "Preparing",
                 "CONVERTED": "Ready", "FAILED": "Failed"}


def _upload_limit() -> str:
    return current_app.config["UPLOAD_RATE_LIMIT"]


@bp.get("/")
@login_required
def index():
    page = request.args.get("page", 1, type=int)
    pagination = db.paginate(visible_documents(current_user).order_by(Document.created_at.desc()),
                             page=page, per_page=25, error_out=False)
    return render_template("documents/list.html", pagination=pagination, labels=STATUS_LABELS,
                           show_owner=current_user.is_admin or current_user.company.workers_can_view_all_documents)


@bp.route("/upload", methods=["GET", "POST"])
@login_required
@limiter.limit(_upload_limit, methods=["POST"])
def upload():
    form = CsrfOnlyForm()
    if request.method == "POST" and form.validate_on_submit():
        try:
            doc = ingest_upload(request.files.get("file"), current_user)
            flash("Upload received. Your document is being prepared for printing.", "success")
            return redirect(url_for("documents.detail", public_id=doc.public_id))
        except ServiceError as e:
            flash(str(e), "danger")
    return render_template("documents/upload.html", form=form, max_mb=current_app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024),
                           extensions=current_app.config["ALLOWED_EXTENSIONS"])


@bp.get("/<uuid:public_id>")
@login_required
def detail(public_id: uuid.UUID):
    doc = get_document_or_404(public_id, current_user)
    pdf = latest_pdf(doc) if doc.status == DocumentStatus.CONVERTED else None
    last = latest_attempt(doc)
    pages = min(pdf.page_count or 0, current_app.config["MAX_PREVIEW_PAGES"]) if pdf else 0
    return render_template("documents/detail.html", doc=doc, pdf=pdf, last=last, preview_pages=pages, labels=STATUS_LABELS,
                           can_modify=can_modify(current_user, doc), form=CsrfOnlyForm())


@bp.get("/<uuid:public_id>/status")
@login_required
def status(public_id: uuid.UUID):
    doc = get_document_or_404(public_id, current_user)
    return jsonify(status=doc.status.value, label=STATUS_LABELS[doc.status.value],
                   error=doc.error_message if doc.status == DocumentStatus.FAILED else None)


def _disposition(kind: str, filename: str) -> str:
    """ASCII fallback plus RFC 5987 UTF-8 name, so any display name is a safe header value."""
    ascii_name = filename.encode("ascii", "ignore").decode().replace('"', "").replace("\\", "") or "document.pdf"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


def _converted_or_404(public_id):
    doc = get_document_or_404(public_id, current_user)
    pdf = latest_pdf(doc)
    if pdf is None or not pdf.storage_key:
        abort(404)
    return doc, pdf


@bp.get("/<uuid:public_id>/pdf")
@login_required
def pdf(public_id: uuid.UUID):
    """Authorised streaming of the converted PDF. There is no public or permanent URL to any stored file."""
    doc, cf = _converted_or_404(public_id)
    try:
        size, chunks = get_storage().open_stream(cf.storage_key)
    except StorageError:
        abort(404)
    as_attachment = request.args.get("download") == "1"
    if as_attachment:
        audit("document.pdf_download", actor=current_user, target_type="document", target_id=doc.public_id)
        db.session.commit()
    stem = doc.original_filename.rsplit(".", 1)[0]
    resp = Response(stream_with_context(chunks), mimetype="application/pdf")
    resp.headers["Content-Length"] = str(size)
    resp.headers["Content-Disposition"] = _disposition("attachment" if as_attachment else "inline", f"{stem}.pdf")
    return resp


@bp.get("/<uuid:public_id>/pages/<int:number>.png")
@login_required
def page_image(public_id: uuid.UUID, number: int):
    doc, cf = _converted_or_404(public_id)
    if number < 1 or number > min(cf.page_count or 0, current_app.config["MAX_PREVIEW_PAGES"]):
        abort(404)
    try:
        data = get_storage().read_bytes(cf.storage_key)
        png = render_page_png(data, number - 1, current_app.config["PREVIEW_WIDTH_PX"])
    except (StorageError, IndexError):
        abort(404)
    except Exception:
        current_app.logger.exception("preview render failed for document %s", doc.public_id)
        abort(500)
    resp = Response(png, mimetype="image/png")
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@bp.post("/<uuid:public_id>/retry")
@login_required
def retry(public_id: uuid.UUID):
    form = CsrfOnlyForm()
    doc = get_document_or_404(public_id, current_user)
    if form.validate_on_submit():
        try:
            request_retry(doc, current_user)
            db.session.commit()
            flash("Conversion queued again.", "success")
        except ServiceError as e:
            db.session.rollback()
            flash(str(e), "danger")
    return redirect(url_for("documents.detail", public_id=doc.public_id))


@bp.post("/<uuid:public_id>/delete")
@login_required
def delete(public_id: uuid.UUID):
    form = CsrfOnlyForm()
    doc = get_document_or_404(public_id, current_user)
    if form.validate_on_submit():
        soft_delete(doc, current_user)
        db.session.commit()
        flash("Document removed from your list.", "success")
        return redirect(url_for("documents.index"))
    return redirect(url_for("documents.detail", public_id=doc.public_id))
