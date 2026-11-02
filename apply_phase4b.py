"""Run once from the project root after unzipping: appends decisions and .gitignore entries (idempotent)."""
from pathlib import Path

ROWS = """| 31 | 2026-11-02 | Agent = package `office_agent/`: UI-free core (`agent_service`, `cloud_client`, `local_database`, `secrets_store`, `printer_manager`) plus thin Tk/pystray shell | Core is testable without Windows; the shell is the only untestable part |
| 32 | 2026-11-02 | Tk owns the main thread; pystray runs detached; network calls run in worker threads and post results back | Avoids frozen windows and cross-thread Tk calls |
| 33 | 2026-11-02 | Credential protected with DPAPI (current user) + app entropy, stored base64 in the local SQLite settings; undecryptable -> re-enrol | Spec: DPAPI; no secret in clear on disk |
| 34 | 2026-11-02 | Per-user installer (no admin), data in %LOCALAPPDATA%\\SeshAgent kept on upgrade, uninstall asks before deleting it; startup via HKCU Run | Matches 'normal app in the user session'; protects job-recovery state |
| 35 | 2026-11-02 | Server URL: https only (http only for localhost), redirects never followed with the credential; default address baked into server_url.txt at build time | Prevents credential leaks to a redirect target or plaintext transport |
| 36 | 2026-11-02 | Virtual printers (Print to PDF, XPS, OneNote, fax) are listed but labelled; Windows status is a hint, never proof of paper | Truthful status |
"""
d = Path("docs/DECISIONS.md")
if "| 31 |" not in d.read_text(encoding="utf-8"):
    s = d.read_text(encoding="utf-8")
    d.write_text(s + ("" if s.endswith("\n") else "\n") + ROWS, encoding="utf-8")
    print("DECISIONS.md updated")
g = Path(".gitignore")
s = g.read_text(encoding="utf-8") if g.exists() else ""
add = [x for x in ("dist/", "build/work/") if x not in s]
if add:
    g.write_text(s + ("" if s.endswith("\n") or not s else "\n") + "\n".join(add) + "\n", encoding="utf-8")
    print(".gitignore updated")
