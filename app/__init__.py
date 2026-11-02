import logging

from flask import Flask, render_template
from sqlalchemy import event
from sqlalchemy.engine import Engine
from werkzeug.middleware.proxy_fix import ProxyFix

from config import INSECURE_DEV_KEY, Config

from .extensions import csrf, db, limiter, login_manager, migrate


def create_app(config_object=None, overrides: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(config_object or Config)
    if overrides:
        app.config.update(overrides)

    if app.config["ENVIRONMENT"] == "production" and app.config["SECRET_KEY"] == INSECURE_DEV_KEY:
        raise RuntimeError("SECRET_KEY must be set to a strong random value in production.")
    if app.config["TRUSTED_PROXY_COUNT"]:
        n = app.config["TRUSTED_PROXY_COUNT"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    db.init_app(app)
    from . import models  # noqa: F401  (register models for migrations)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    @event.listens_for(Engine, "connect")
    def _sqlite_fk_pragma(dbapi_conn, _):  # SQLite ignores foreign keys unless asked
        if dbapi_conn.__class__.__module__.startswith("sqlite3"):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    from .admin import bp as admin_bp
    from .auth import bp as auth_bp
    from .dashboard.routes import bp as dashboard_bp
    from .documents import bp as documents_bp
    from .agents import bp as agents_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(documents_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(agents_bp)
    csrf.exempt(agents_bp)  # machine API: bearer credential, no cookies
    from .services.agents import is_online
    app.jinja_env.globals["agent_online"] = is_online

    from .cli import register_cli
    register_cli(app)

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Content-Security-Policy",
                                "default-src 'self'; style-src 'self' https://cdn.jsdelivr.net; script-src 'self' https://cdn.jsdelivr.net; "
                                "img-src 'self' data:; frame-ancestors 'none'; form-action 'self'")
        if app.config["ENVIRONMENT"] == "production":
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if "Cache-Control" not in resp.headers:
            resp.headers["Cache-Control"] = "no-store"
        return resp

    messages = {400: "The request could not be processed (the page may have expired — reload and try again).",
                403: "You do not have permission to do that.", 404: "That page or item was not found.",
                413: "The file is larger than the allowed size.", 429: "Too many attempts. Please wait a minute and try again."}

    def _handler(err):
        code = getattr(err, "code", 500)
        return render_template("error.html", code=code, message=messages.get(code, "Something went wrong.")), code

    for code in (400, 403, 404, 413, 429):
        app.register_error_handler(code, _handler)

    @app.errorhandler(500)
    def _server_error(err):  # no stack traces or internals for users; details go to server logs
        db.session.rollback()
        return render_template("error.html", code=500, message="Something went wrong on our side. Please try again."), 500

    return app
