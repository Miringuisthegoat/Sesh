"""Run once from the project root after unzipping the overlay: python apply_landing.py
Makes "/" show the public landing page to signed-out visitors (signed-in users still get the dashboard), points the
login-page logo at it, updates the tests that expected "/" to redirect, and logs the decision. Idempotent; stops loudly
if an anchor is missing."""
import re
from pathlib import Path

EDITS = [
    ("app/dashboard/routes.py", "from ..services.offices import get_company_office\n",
     "from ..services.offices import get_company_office\nfrom ..services.users import company_exists\n"),
    ("app/dashboard/routes.py", '@bp.get("/")\n@login_required\ndef index():\n',
     '@bp.get("/")\ndef index():\n'
     '    if not current_user.is_authenticated:  # signed-out visitors get the public landing page\n'
     '        return render_template("landing.html", setup_open=not company_exists())\n'),
    ("app/templates/base.html",
     """<a class="sesh-brand auth-brand" href="{{ url_for('auth.login') }}">""",
     """<a class="sesh-brand auth-brand" href="{{ url_for('dashboard.index') }}">"""),
    ("tests/test_roles_isolation.py",
     'def test_anonymous_redirected_from_everything(client, acme):\n    for url in ["/"] + ADMIN_PAGES:',
     'def test_anonymous_redirected_from_everything(client, acme):\n    for url in ADMIN_PAGES:'),
]

# "/" no longer redirects signed-out visitors: these assertions now check that they do not get the dashboard.
OLD_ASSERT = re.compile(r'assert client\.get\("/", follow_redirects=False\)\.status_code == 302')
NEW_ASSERT = 'assert b"Welcome back" not in client.get("/").data'
TEST_FILES = ["tests/test_auth.py"]

ROW = ('| 61 | 2026-11-10 | Public landing page at "/" for signed-out visitors (`dashboard.index` renders `landing.html` when nobody is signed in; '
       'signed-in users still get the dashboard). Sign-up is offered only while `/setup` is open (no company yet); otherwise the page says accounts '
       'are created by the company administrator | MVP is one company and no self-service sign-up was specified. Tests that expected a 302 from "/" '
       'now check that signed-out visitors do not see the dashboard |\n')


def main():
    for path, old, new in EDITS:
        p = Path(path)
        s = p.read_text(encoding="utf-8")
        if new in s:
            print("already applied:", path, repr(old[:45]))
            continue
        if s.count(old) != 1:
            raise SystemExit(f"ANCHOR NOT FOUND (or not unique) in {path}: {old[:70]!r}")
        p.write_text(s.replace(old, new), encoding="utf-8")
        print("patched:", path)
    for path in TEST_FILES:
        p = Path(path)
        s = p.read_text(encoding="utf-8")
        s2, n = OLD_ASSERT.subn(NEW_ASSERT, s)
        if n:
            p.write_text(s2, encoding="utf-8")
            print(f"patched: {path} ({n} assertion(s))")
        else:
            print("already applied:", path)
    d = Path("docs/DECISIONS.md")
    s = d.read_text(encoding="utf-8")
    if "| 61 |" in s:
        print("already applied: docs/DECISIONS.md")
    else:
        d.write_text(s + ("" if s.endswith("\n") else "\n") + ROW, encoding="utf-8")
        print("DECISIONS.md updated")


if __name__ == "__main__":
    main()
