"""Run once from the project root after unzipping the overlay: python apply_phase5c.py
Targeted edits to existing files. Idempotent; stops loudly if an anchor is missing."""
from pathlib import Path

EDITS = [
    # ---- register the blueprint ---------------------------------------------------------------------------------
    ("app/__init__.py", "    from .agents import bp as agents_bp\n",
     "    from .agents import bp as agents_bp\n    from .printing import bp as printing_bp\n"),
    ("app/__init__.py", "    app.register_blueprint(agents_bp)\n",
     "    app.register_blueprint(agents_bp)\n    app.register_blueprint(printing_bp)\n"),
    # ---- navigation ---------------------------------------------------------------------------------------------
    ("app/templates/base.html",
     """        <li class="nav-item"><a class="nav-link" href="{{ url_for('documents.index') }}">Documents</a></li>\n""",
     """        <li class="nav-item"><a class="nav-link" href="{{ url_for('documents.index') }}">Documents</a></li>\n"""
     """        <li class="nav-item"><a class="nav-link" href="{{ url_for('printing.queue') }}">Print jobs</a></li>\n"""),
    # ---- document page: Print button, honest wording ------------------------------------------------------------
    ("app/templates/documents/detail.html",
     """<a class="btn btn-outline-secondary" href="{{ url_for('documents.pdf', public_id=doc.public_id, download=1) }}">Download PDF</a>\n""",
     """<a class="btn btn-outline-secondary" href="{{ url_for('documents.pdf', public_id=doc.public_id, download=1) }}">Download PDF</a>\n"""
     """    {% if can_modify %}<a class="btn btn-primary" href="{{ url_for('printing.new', public_id=doc.public_id) }}">Print</a>{% endif %}\n"""),
    ("app/templates/documents/detail.html",
     "it is the exact PDF that would be printed. Printing is not available yet in this version.</p>",
     "it is the exact PDF that will be printed.</p>"),
    # ---- dashboard route ----------------------------------------------------------------------------------------
    ("app/dashboard/routes.py", "from ..services.documents import visible_documents\n",
     "from ..services.documents import visible_documents\nfrom ..models import PrintJob, PrintJobStatus\n"
     "from ..printing.labels import JOB_LABELS\nfrom ..services.print_jobs import PENDING, visible_print_jobs\n"),
    ("app/dashboard/routes.py",
     '    return render_template("dashboard.html", office=office, agent=agent, printer=printer, stats=stats,\n'
     '                           recent=recent, in_progress=in_progress, failed=failed)\n',
     '''    jobs = visible_print_jobs(current_user)
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
'''),
    # ---- dashboard template -------------------------------------------------------------------------------------
    ("app/templates/dashboard.html", '{% from "documents/_status.html" import badge %}\n',
     '{% from "documents/_status.html" import badge %}\n{% from "printing/_status.html" import badge as job_badge %}\n'),
    ("app/templates/dashboard.html",
     '<p class="mb-0">{% if agent %}Registered: {{ agent.name }}{% else %}No agent registered yet.{% endif %}</p>',
     '''<p class="mb-0">{% if agent %}{{ agent.name }} ·
      {% if agent.status.value == 'PENDING' %}<span class="badge text-bg-secondary">Waiting for registration</span>
      {% elif agent_online(agent) %}<span class="badge text-bg-success">Connected</span>
      {% else %}<span class="badge text-bg-warning">Not connected</span>{% endif %}
      {% else %}No agent registered yet.{% endif %}</p>'''),
    ("app/templates/dashboard.html",
     '<p class="mb-0">{% if printer %}Registered: {{ printer.display_name or printer.windows_name }}{% else %}No printer registered yet.{% endif %}</p>',
     '''<p class="mb-0">{% if printer %}{{ printer.display_name or printer.windows_name }}
      <span class="small text-muted d-block">Windows reports: {{ {'AVAILABLE':'no problem','OFFLINE':'offline','ERROR':'needs attention','UNKNOWN':'status unknown'}[printer.availability.value] }} (a hint only, shown separately from the computer's connection)</span>
      {% else %}No printer registered yet.{% endif %}</p>'''),
    ("app/templates/dashboard.html", '<h2 class="h6 text-uppercase text-muted">Workers</h2>',
     '<h2 class="h6 text-uppercase text-muted">Workers and printing</h2>'),
    ("app/templates/dashboard.html",
     '<p class="mb-0">{{ stats.active_workers }} active, {{ stats.inactive_workers }} inactive</p>',
     '<p class="mb-0">{{ stats.active_workers }} active, {{ stats.inactive_workers }} inactive</p>\n'
     '    <p class="mb-0 small text-muted">{{ stats.jobs_total }} print job{{ \'s\' if stats.jobs_total != 1 }} in total · {{ stats.jobs_attention }} failed or uncertain</p>'),
    ("app/templates/dashboard.html",
     '<p class="text-muted small mt-4">Printing is not available yet in this version.</p>',
     '''<div class="card mt-3"><div class="card-body">
  <div class="d-flex justify-content-between align-items-center mb-2">
    <h2 class="h6 text-uppercase text-muted mb-0">Recent print jobs</h2>
    <a class="btn btn-sm btn-outline-secondary" href="{{ url_for('printing.queue') }}">All jobs</a>
  </div>
  <p class="small text-muted mb-2">{{ jobs_waiting }} waiting or in progress · <a href="{{ url_for('printing.attention') }}">{{ jobs_attention }} failed or need checking</a></p>
  {% if recent_jobs %}
  <ul class="list-unstyled mb-0">
    {% for j in recent_jobs %}
    <li class="d-flex justify-content-between align-items-center py-1 border-top">
      <a href="{{ url_for('printing.detail', public_id=j.public_id) }}" class="text-break">{{ j.converted_file.document.original_filename }}</a>
      {{ job_badge(j.status.value, job_labels) }}
    </li>
    {% endfor %}
  </ul>
  {% else %}<p class="mb-0">No print jobs yet.</p>{% endif %}
  <p class="small text-muted mb-0 mt-2">"Sent to printer" means Windows accepted the job. It does not prove that paper came out.</p>
</div></div>'''),
]

ROWS = """| 48 | 2026-11-03 | Print UI under /print: queue, history, needs-attention lists; new/<document>; job detail with cancel, submit again and (admin) settle-uncertain. Same visibility as the service: workers see their own jobs, admins the company's | UI adds no rules of its own; services/print_jobs.py decides |
| 49 | 2026-11-03 | Printing a document needs owner or admin rights (can_modify). The company 'workers may view all documents' policy never grants printing | View is not print: avoids using a colleague's confidential document |
| 50 | 2026-11-03 | Print form offers only what the agent passes to SumatraPDF (copies, paper, colour, orientation), says the driver has the final word, and hides colour when Windows reports a mono printer | Spec: no controls that cannot be honoured reliably |
| 51 | 2026-11-03 | Each print form carries a random request id used as the idempotency key, so double-clicks and back-then-resubmit create one job | Duplicate-print protection at the UI edge |
| 52 | 2026-11-03 | Wording: SUBMITTED = 'Sent to printer' with an explanation; COMPLETED only after admin confirmation. Agent connectivity and Windows printer status are shown separately. Jobs may be queued while the agent is offline, with a visible warning | Truthful status; no fake 'printer online' |
"""


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
    d = Path("docs/DECISIONS.md")
    s = d.read_text(encoding="utf-8")
    if "| 48 |" in s:
        print("already applied: docs/DECISIONS.md")
    else:
        d.write_text(s + ("" if s.endswith("\n") else "\n") + ROWS, encoding="utf-8")
        print("DECISIONS.md updated")


if __name__ == "__main__":
    main()
