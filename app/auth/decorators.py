from functools import wraps

from flask import abort
from flask_login import current_user, login_required


def admin_required(view):
    """Server-side authorization. Hiding a menu item is not security."""
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapped
