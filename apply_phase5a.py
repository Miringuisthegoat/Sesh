"""Run once from the project root after unzipping the overlay: python apply_phase5a.py
Adds two config settings and appends decisions 37-42. Idempotent; stops loudly if an anchor is missing."""
from pathlib import Path

EDITS = [
    ("config.py", '    MAX_CONVERSION_RETRIES = _int("MAX_CONVERSION_RETRIES", 3)\n',
     '    MAX_CONVERSION_RETRIES = _int("MAX_CONVERSION_RETRIES", 3)\n'
     '    MAX_JOB_ATTEMPTS = _int("MAX_JOB_ATTEMPTS", 3)  # claims of one print job before it is failed as AGENT_LOST\n'
     '    MAX_PENDING_JOBS_PER_USER = _int("MAX_PENDING_JOBS_PER_USER", 20)\n'),
    (".env.example", "MAX_CONVERSION_RETRIES=3\n",
     "MAX_CONVERSION_RETRIES=3\nMAX_JOB_ATTEMPTS=3                 # times a print job may be claimed before failing as AGENT_LOST\n"
     "MAX_PENDING_JOBS_PER_USER=20\n"),
]

ROWS = """| 37 | 2026-11-03 | Phase 5 split in three: 5a server job core (state machine, create, claim, lease recovery, cancel, resolve; this drop), 5b agent job API + agent print loop, 5c web UI (submit, queue, history, admin) | Each part is testable alone; the agent cannot print until 5a's rules exist |
| 38 | 2026-11-03 | Status changes go through one transition table and a conditional UPDATE (`WHERE status = seen`); agents may only set DOWNLOADING, DOWNLOADED, SUBMITTED_TO_PRINTER, COMPLETED, FAILED, UNKNOWN or hand a job back to QUEUED | Illegal or racing updates are refused by the server, not trusted to clients |
| 39 | 2026-11-03 | Claiming = atomic conditional UPDATE plus a one-time claim token (only its SHA-256 stored). Every agent report needs the token and renews the lease | A stale or foreign agent cannot touch a job it does not hold |
| 40 | 2026-11-03 | Lease expiry: CLAIMED/DOWNLOADING -> requeue (after MAX_JOB_ATTEMPTS -> FAILED); DOWNLOADED -> UNKNOWN, never auto-requeued. Attempts are counted from audit rows (no new column) | Printing is a physical side effect: do not risk a duplicate print |
| 41 | 2026-11-03 | Users may cancel only QUEUED/CLAIMED/DOWNLOADING jobs; DOWNLOADED is the point of no return. The agent must print only after the server accepted DOWNLOADED | Cancel can never race with an already started print |
| 42 | 2026-11-03 | UNKNOWN is settled by an administrator ('printed' -> COMPLETED, 'not printed' -> FAILED) or by the original agent proving SUBMITTED. Resubmit always creates a NEW job and is allowed only for FAILED/CANCELLED | No automatic reprint; COMPLETED only with a reliable signal |
"""


def main():
    for path, old, new in EDITS:
        p = Path(path)
        s = p.read_text(encoding="utf-8")
        if new in s:
            print("already applied:", path)
            continue
        if s.count(old) != 1:
            raise SystemExit(f"ANCHOR NOT FOUND (or not unique) in {path}: {old[:60]!r}")
        p.write_text(s.replace(old, new), encoding="utf-8")
        print("patched:", path)
    d = Path("docs/DECISIONS.md")
    s = d.read_text(encoding="utf-8")
    if "| 37 |" in s:
        print("already applied: docs/DECISIONS.md")
    else:
        d.write_text(s + ("" if s.endswith("\n") else "\n") + ROWS, encoding="utf-8")
        print("DECISIONS.md updated")


if __name__ == "__main__":
    main()
