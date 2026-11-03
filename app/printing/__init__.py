from flask import Blueprint

bp = Blueprint("printing", __name__, url_prefix="/print")

from . import routes  # noqa: E402,F401
