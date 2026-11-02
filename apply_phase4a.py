"""Run from the project root AFTER unzipping the overlay: python apply_phase4a.py
Applies small targeted edits to existing files. Idempotent; stops loudly if an anchor is missing."""
from pathlib import Path

EDITS = [
    ("app/__init__.py", "    from .documents import bp as documents_bp\n",
     "    from .documents import bp as documents_bp\n    from .agents import bp as agents_bp\n"),
    ("app/__init__.py", "    app.register_blueprint(admin_bp)\n",
     "    app.register_blueprint(admin_bp)\n    app.register_blueprint(agents_bp)\n"
     "    csrf.exempt(agents_bp)  # machine API: bearer credential, no cookies\n"
     "    from .services.agents import is_online\n    app.jinja_env.globals[\"agent_online\"] = is_online\n"),
    ("config.py", '    AGENT_OFFLINE_AFTER_SECONDS = _int("AGENT_OFFLINE_AFTER_SECONDS", 90)\n',
     '    AGENT_OFFLINE_AFTER_SECONDS = _int("AGENT_OFFLINE_AFTER_SECONDS", 90)\n'
     '    AGENT_ENROLLMENT_HOURS = _int("AGENT_ENROLLMENT_HOURS", 24)\n'
     '    AGENT_REGISTER_RATE_LIMIT = os.environ.get("AGENT_REGISTER_RATE_LIMIT", "10 per minute")\n'),
    ("app/admin/routes.py", "from flask import Blueprint, current_app, flash, redirect, render_template, url_for\n",
     "from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for\n"),
    ("app/admin/routes.py", "from ..services.audit import audit\n",
     "from ..services.agents import create_enrollment, revoke_agent\nfrom ..services.audit import audit\n"),
]
APPENDS = [
    ("app/admin/routes.py", "agent_enroll", '''

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
'''),
    (".env.example", "AGENT_ENROLLMENT_HOURS", "\nAGENT_ENROLLMENT_HOURS=24           # how long an enrolment token stays valid\nAGENT_REGISTER_RATE_LIMIT=10 per minute\n"),
    ("docs/DECISIONS.md", "| 26 |", """| 26 | 2026-11-02 | Phase 4 split: 4a = backend agent API + admin enrolment; 4b = Windows agent app (setup, DPAPI, printers, tray, packaging); job claim/print loop follows Phase 5 server endpoints | Agent cannot download/print jobs until the print-job state machine and claim API exist |
| 27 | 2026-11-02 | Enrolment: admin generates a single-use token (24 h, shown once, hash stored); agent exchanges it at POST /api/agent/v1/register for a long-lived credential (hash stored). No session codes | Spec: secure one-time registration, revocable machine identity |
| 28 | 2026-11-02 | Agent API is bearer-auth JSON under /api/agent/v1, CSRF-exempt (no cookies); human routes keep CSRF | Machine clients cannot do CSRF; credential in header is not ambient |
| 29 | 2026-11-02 | Agent connectivity (last_seen_at) and printer availability (reported per heartbeat; missing = OFFLINE) are separate fields; MVP printer limit enforced when the agent selects printers | Spec: never show printer online just because agent is online |
| 30 | 2026-11-02 | Credential rotation is immediate (old credential dies at once). If the rotate response is lost the agent is locked out and the admin must revoke and re-enrol | Simplicity; an overlap window is a later hardening option |
"""),
]

def main():
    for path, old, new in EDITS:
        p = Path(path); s = p.read_text(encoding="utf-8")
        if new in s:
            print("already applied:", path, repr(old[:40])); continue
        if s.count(old) != 1:
            raise SystemExit(f"ANCHOR NOT FOUND (or not unique) in {path}: {old[:60]!r}")
        p.write_text(s.replace(old, new), encoding="utf-8"); print("patched:", path)
    for path, marker, text in APPENDS:
        p = Path(path); s = p.read_text(encoding="utf-8")
        if marker in s:
            print("already applied:", path, marker); continue
        if not s.endswith("\n"): s += "\n"
        p.write_text(s + text.lstrip("\n") if path != "app/admin/routes.py" else s + text, encoding="utf-8"); print("appended:", path)

if __name__ == "__main__":
    main()
