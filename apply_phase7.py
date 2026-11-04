"""Run once from the project root after unzipping the overlay: python apply_phase7.py
Targeted edits to existing files, appended code/docs, and one generated migration. Idempotent; stops loudly if an anchor is missing."""
import re
from pathlib import Path

EDITS = [
    # ---- purged_at column ---------------------------------------------------------------------------------------
    ("app/models/document.py",
     "    deleted_at = mapped_column(sa.DateTime(timezone=True))  # soft delete; retention job purges files later\n",
     "    deleted_at = mapped_column(sa.DateTime(timezone=True))  # soft delete; retention job purges files later\n"
     "    purged_at = mapped_column(sa.DateTime(timezone=True))   # stored files deleted by the retention purge (rows are kept)\n"),
    # ---- config -------------------------------------------------------------------------------------------------
    ("config.py", '    MAX_PENDING_JOBS_PER_USER = _int("MAX_PENDING_JOBS_PER_USER", 20)\n',
     '    MAX_PENDING_JOBS_PER_USER = _int("MAX_PENDING_JOBS_PER_USER", 20)\n'
     '    AGENT_EVENT_RETENTION_DAYS = _int("AGENT_EVENT_RETENTION_DAYS", 30)  # old agent connection events are pruned\n'),
    # ---- app factory: health blueprint + CSP without the CDN ------------------------------------------------------
    ("app/__init__.py", "    app.register_blueprint(printing_bp)\n",
     "    app.register_blueprint(printing_bp)\n    from .health import bp as health_bp\n    app.register_blueprint(health_bp)\n"),
    ("app/__init__.py",
     "\"default-src 'self'; style-src 'self' https://cdn.jsdelivr.net; script-src 'self' https://cdn.jsdelivr.net; \"",
     "\"default-src 'self'; style-src 'self'; script-src 'self'; \""),
    # ---- Bootstrap served locally ---------------------------------------------------------------------------------
    ("app/templates/base.html",
     '<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">',
     '<link href="{{ url_for(\'static\', filename=\'vendor/bootstrap/bootstrap.min.css\') }}" rel="stylesheet">'),
    ("app/templates/base.html",
     '<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>',
     '<script src="{{ url_for(\'static\', filename=\'vendor/bootstrap/bootstrap.bundle.min.js\') }}"></script>'),
    # inline style attributes are blocked by the CSP: use a class
    ("app/templates/admin/company.html", '<div class="card" style="max-width:640px">', '<div class="card form-card">'),
    # ---- account menu link ----------------------------------------------------------------------------------------
    ("app/templates/base.html", '      <div class="sidebar-user">\n',
     '      <nav class="sesh-nav mb-2" aria-label="Account">\n'
     '        <a class="sesh-nav-link {{ \'is-active\' if endpoint == \'auth.change_password\' else \'\' }}" href="{{ url_for(\'auth.change_password\') }}">\n'
     '          <span class="nav-mark" aria-hidden="true"></span><span>Change password</span>\n'
     '        </a>\n      </nav>\n      <div class="sidebar-user">\n'),
    # ---- password change wiring ------------------------------------------------------------------------------------
    ("app/auth/routes.py", "from ..services.users import company_exists, create_company_with_admin\n",
     "from ..services.users import change_own_password, company_exists, create_company_with_admin\n"),
    ("app/auth/routes.py", "from .forms import LoginForm, SetupForm\n", "from .forms import ChangePasswordForm, LoginForm, SetupForm\n"),
    # ---- expired enrolment must not hold the only agent slot --------------------------------------------------------
    ("app/services/agents.py",
     "def create_enrollment(office, *, name: str | None, actor) -> tuple[Agent, str]:\n",
     '''def _expire_stale_enrollments(office) -> None:
    """A never-used enrolment whose token has expired must not hold the office's single agent slot forever."""
    now = utcnow()
    for a in db.session.scalars(select(Agent).where(Agent.office_id == office.id, Agent.status == AgentStatus.PENDING)).all():
        exp = _aware(a.enrollment_expires_at)
        if exp is not None and exp < now:
            a.status = AgentStatus.REVOKED
            a.revoked_at = now
            a.enrollment_token_hash = a.enrollment_expires_at = None
            _event(a, "ENROLLMENT_EXPIRED", "Enrolment token expired before it was used.")
            audit("agent.enrollment_expired", company_id=a.company_id, target_type="agent", target_id=a.public_id)
    db.session.flush()


def create_enrollment(office, *, name: str | None, actor) -> tuple[Agent, str]:
'''),
    ("app/services/agents.py", '    ensure_can_add_agent(office)\n    token = "se_" + secrets.token_urlsafe(24)\n',
     '    _expire_stale_enrollments(office)\n    ensure_can_add_agent(office)\n    token = "se_" + secrets.token_urlsafe(24)\n'),
    # ---- only extensions the validator really supports may be allowed by configuration -------------------------------
    ("app/services/documents.py", "from ..conversion.validation import mime_acceptable, validate_source\n",
     "from ..conversion.validation import SUPPORTED, mime_acceptable, validate_source\n"),
    ("app/services/documents.py", "    if not ext or ext not in allowed:\n", "    if not ext or ext not in allowed or ext not in SUPPORTED:\n"),
    # ---- CLI --------------------------------------------------------------------------------------------------------
    ("app/cli.py", "def register_cli(app):\n    app.cli.add_command(create_company)\n    app.cli.add_command(run_worker_cmd)\n",
     '''@click.command("purge-expired")
@click.option("--dry-run", is_flag=True, help="Show what would be removed without changing anything.")
@with_appcontext
def purge_expired_cmd(dry_run):
    """Delete stored files of documents older than the company's retention period (rows and audit log are kept)."""
    from .services.retention import purge_expired
    r = purge_expired(dry_run=dry_run)
    click.echo(f"{'Would purge' if dry_run else 'Purged'} {r['documents']} document(s) ({r['objects']} stored file(s)); "
               f"skipped {r['skipped']} still in use; {r['errors']} error(s); "
               f"{'would remove' if dry_run else 'removed'} {r['agent_events']} old agent event(s).")
    if r["errors"]:
        raise SystemExit(1)


def register_cli(app):
    app.cli.add_command(create_company)
    app.cli.add_command(run_worker_cmd)
    app.cli.add_command(purge_expired_cmd)
'''),
    # ---- README -------------------------------------------------------------------------------------------------------
    ("README.md", "Status: Phase 3 (document workflow). Working now:", "Status: Phase 7 (hardening and pilot readiness). Working now:"),
    ("README.md", "Not built yet (so not shown as working): Windows agent, print jobs. ",
     "Also built: Windows agent, print jobs, retention purge, health check, password change. NOT yet verified on real hardware: printing and the installer on clean Windows (see docs/PILOT_CHECKLIST.md). "),
]

APPENDS = [
    ("app/auth/forms.py", "class ChangePasswordForm", '''

class ChangePasswordForm(FlaskForm):
    current = PasswordField("Current password", validators=[DataRequired(), Length(max=128)])
    password = PasswordField("New password", validators=[DataRequired(), Length(max=128)])
    confirm = PasswordField("Confirm new password", validators=[DataRequired(), EqualTo("password", "Passwords do not match.")])
'''),
    ("app/services/users.py", "def change_own_password", '''

def change_own_password(user: User, current_password: str, new_password: str) -> None:
    """Self-service change. Caller commits. Never log either password."""
    if not user.check_password(current_password or ""):
        raise ServiceError("Your current password is not correct.")
    if current_password == new_password:
        raise ServiceError("Choose a password that is different from the current one.")
    validate_password(new_password, user.email)
    user.set_password(new_password)
    audit("user.password_changed", actor=user, target_type="user", target_id=user.public_id)
'''),
    ("app/auth/routes.py", "def change_password", '''

@bp.route("/account/password", methods=["GET", "POST"])
@login_required
@limiter.limit(_login_limit, methods=["POST"])
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        try:
            change_own_password(current_user, form.current.data, form.password.data)
            db.session.commit()
            flash("Your password was changed.", "success")
            return redirect(url_for("dashboard.index"))
        except ServiceError as e:
            db.session.rollback()
            flash(str(e), "danger")
    return render_template("auth/password.html", form=form)
'''),
    ("app/static/app.css", ".form-card {", "\n.form-card { max-width: 640px; }\n"),
    (".env.example", "AGENT_EVENT_RETENTION_DAYS", "\nAGENT_EVENT_RETENTION_DAYS=30       # connection events older than this are pruned by `flask purge-expired`\n"),
    ("README.md", "## Operations (Phase 7)", '''

## Operations (Phase 7)
Deploy: `docs/DEPLOYMENT.md` (files in `deploy/`). Backups: `docs/BACKUP_RESTORE.md`. Problems: `docs/TROUBLESHOOTING.md`.
Before a pilot: `docs/SECURITY_REVIEW.md` and `docs/PILOT_CHECKLIST.md`. Daily maintenance: `python -m flask purge-expired` (`--dry-run` first).
Health probe: `GET /healthz`. Performance guide: `python -m scripts.perf_check`.
'''),
]

ROWS = """| 53 | 2026-11-04 | Retention purge (`flask purge-expired`, daily timer): deletes stored files of documents older than the company's retention_days, keeps DB rows and audit log, sets documents.purged_at (+ deleted_at). Skips documents with conversion in flight or print jobs that are queued, held by the agent or UNKNOWN | Meets the retention promise without losing print history; never pulls a file from under a live job |
| 54 | 2026-11-04 | AgentEvent rows older than AGENT_EVENT_RETENTION_DAYS (30) are pruned by the same command; audit logs are never pruned | Bounded growth for noisy data, permanent trail for important actions |
| 55 | 2026-11-04 | Bootstrap 5.3.3 is vendored in app/static/vendor; CSP is now `self` only (no CDN); the one inline style attribute was replaced by a class | Closes the CDN supply-chain/availability gap (decision 14); offline-capable |
| 56 | 2026-11-04 | GET /healthz: unauthenticated, runs `SELECT 1`, returns only {status} (200/503) | Monitors and Nginx can probe without leaking details |
| 57 | 2026-11-04 | Generating a new enrolment token first revokes expired never-used enrolments of that office (audit `agent.enrollment_expired`) | An abandoned token no longer blocks the single agent slot |
| 58 | 2026-11-04 | Workers/admins can change their own password (`/account/password`, current password required, rate limited). Other sessions are NOT revoked | Closes a known gap; session revocation needs a schema change, listed as residual risk |
| 59 | 2026-11-04 | ALLOWED_EXTENSIONS can only narrow the supported set; an extension unknown to the validator is rejected (was a 500) | Misconfiguration cannot create a crash path |
| 60 | 2026-11-04 | Accepted residual risks for the pilot (docs/SECURITY_REVIEW.md): no hard per-conversion timeout/memory cap (mitigated by systemd MemoryMax + lease recovery), no account lockout beyond rate limiting, SumatraPDF licence review open | Each has a stated mitigation and must be re-evaluated before wider rollout |
"""


def make_migration() -> None:
    vdir = Path("migrations/versions")
    files = list(vdir.glob("*.py"))
    if any("purged_at" in f.read_text(encoding="utf-8") for f in files):
        print("already applied: migration for purged_at")
        return
    revs, downs = set(), set()
    for f in files:
        s = f.read_text(encoding="utf-8")
        r = re.search(r'''^revision = ['"]([^'"]+)['"]''', s, re.M)
        d = re.search(r'''^down_revision = (?:None|['"]([^'"]+)['"])''', s, re.M)
        if r:
            revs.add(r.group(1))
        if d and d.group(1):
            downs.add(d.group(1))
    heads = revs - downs
    if len(heads) != 1:
        raise SystemExit(f"Expected exactly one migration head, found {sorted(heads)}")
    head = heads.pop()
    (vdir / "b7d2f4a91c05_phase7_document_purged_at.py").write_text(f'''"""phase 7: documents.purged_at

Revision ID: b7d2f4a91c05
Revises: {head}
Create Date: 2026-11-04 09:00:00

"""
from alembic import op
import sqlalchemy as sa

revision = 'b7d2f4a91c05'
down_revision = '{head}'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('purged_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_column('purged_at')
''', encoding="utf-8")
    print("created migration b7d2f4a91c05 (down_revision", head + ")")


def main():
    for path, old, new in EDITS:
        p = Path(path)
        s = p.read_text(encoding="utf-8")
        if new in s:
            print("already applied:", path, repr(old[:40]))
            continue
        if s.count(old) != 1:
            raise SystemExit(f"ANCHOR NOT FOUND (or not unique) in {path}: {old[:70]!r}")
        p.write_text(s.replace(old, new), encoding="utf-8")
        print("patched:", path)
    for path, marker, text in APPENDS:
        p = Path(path)
        s = p.read_text(encoding="utf-8")
        if marker in s:
            print("already applied:", path, marker)
            continue
        if not s.endswith("\n"):
            s += "\n"
        p.write_text(s + text, encoding="utf-8")
        print("appended:", path)
    make_migration()
    d = Path("docs/DECISIONS.md")
    s = d.read_text(encoding="utf-8")
    if "| 53 |" in s:
        print("already applied: docs/DECISIONS.md")
    else:
        d.write_text(s + ("" if s.endswith("\n") else "\n") + ROWS, encoding="utf-8")
        print("DECISIONS.md updated")


if __name__ == "__main__":
    main()
