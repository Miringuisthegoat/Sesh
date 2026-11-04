"""Liveness/readiness probe for the reverse proxy and uptime monitors. No authentication, no details in the response."""
import logging

from flask import Blueprint, jsonify
from sqlalchemy import select

from .extensions import db

bp = Blueprint("health", __name__)
log = logging.getLogger(__name__)


def _check_db() -> None:
    db.session.execute(select(1))


@bp.get("/healthz")
def healthz():
    try:
        _check_db()
    except Exception:
        db.session.rollback()
        log.exception("health check: database unavailable")
        return jsonify(status="error"), 503
    return jsonify(status="ok")
