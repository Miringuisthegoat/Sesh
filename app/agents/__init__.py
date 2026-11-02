from flask import Blueprint

bp = Blueprint("agents", __name__, url_prefix="/api/agent/v1")

from . import routes  # noqa: E402,F401
