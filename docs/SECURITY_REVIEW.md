# Security review - Phase 7 (2026-11-04)
Method: code reading of the whole repository plus the automated tests. Not a penetration test. Nothing here was run against a live server. `pip-audit` was NOT run (see actions).

## Controls found in place (and where they are tested)
- Passwords: scrypt hashes, min length 10, max 128; generic login error plus dummy-hash timing for unknown emails; open-redirect guard (`tests/test_auth.py`).
- Sessions/CSRF: HttpOnly + SameSite=Lax, Secure in production, `session_protection=strong`, CSRF on every browser POST, users reloaded per request so deactivation is immediate.
- Authorisation: server-side `admin_required`; every company-owned lookup filters by `company_id`; cross-tenant and not-yours both return 404 (`tests/test_roles_isolation.py`, document and print route tests).
- Files: random storage keys, strict key regex, upload validated twice (extension, MIME, signature, ZIP bomb limits, image pixel limit, OLE/password-protected rejection), sanitised display names, private storage, no public or permanent URLs.
- Agent: credentials and enrolment tokens stored as SHA-256 hashes; claim tokens hashed and compared in constant time; one-time token reveal; revocation immediate; logs redact `sa_`/`se_`/Bearer.
- Printing safety: transition table + conditional UPDATE; DOWNLOADED is the cancel point of no return; write-ahead journal; UNKNOWN never reprinted automatically.
- Headers: nosniff, DENY framing, same-origin referrer, strict CSP (now `self` only), HSTS in production, `no-store` on dynamic pages.

## Changed in this phase
CDN removed (Bootstrap vendored, CSP `self` only); inline style attribute removed; `/healthz` added; self-service password change (rate limited); expired enrolment frees the agent slot; unsupported extension in `ALLOWED_EXTENSIONS` rejected instead of crashing; retention purge and event pruning added.

## Findings and residual risks
| # | Finding | Severity | Status / mitigation |
|---|---|---|---|
| 1 | `/setup` is open until the first company exists | High if exposed early | Create the company by CLI before opening the site (DEPLOYMENT step 5) |
| 2 | Behind a proxy, `TRUSTED_PROXY_COUNT=0` makes all users share one IP: rate limits lock everyone out and audit IPs are wrong | Medium | Documented as required setting |
| 3 | Rate limiter uses memory storage by default: limits are per gunicorn worker | Medium | Use Redis (`RATELIMIT_STORAGE_URI`); Nginx `limit_req` added as a second layer |
| 4 | No account lockout, only rate limiting | Low-Medium | Accepted for pilot; 10/min per IP and 10+ char passwords |
| 5 | Other sessions stay valid after a password change/reset until the 12 h expiry (deactivation is immediate) | Low-Medium | Accepted; fix needs a `session_epoch` column (next hardening step) |
| 6 | No hard timeout or memory cap per conversion; only size/cell/paragraph limits and the lease | Medium | systemd `MemoryMax=1500M` on the worker; lease recovery retries then fails; revisit with subprocess isolation |
| 7 | Preview renders on demand with no cache; each view costs CPU (limited to 30 pages) | Low | Authenticated only; Nginx `limit_req`; add cache if users complain |
| 8 | Agent installer is unsigned: SmartScreen warns; SumatraPDF licence (GPLv3) review still open and it is not bundled | Medium (trust/legal) | Decide on code signing and bundling before wider rollout |
| 9 | Real printer, installer on clean Windows 10/11, and R2 were not tested in this project (storage tests use a mock) | High for go-live | Gate in `docs/PILOT_CHECKLIST.md` |
| 10 | Purged documents keep their (dead) `storage_key` for the source file | Info | Rows are kept on purpose; key is never reused |
| 11 | Audit log rows are never pruned and contain emails of failed logins | Low | Define your own policy; no passwords are ever stored |

## Actions the owner must take
1. `pip install pip-audit && pip-audit -r requirements.txt` (and `-r requirements-agent.txt`) and act on findings; the pinned versions have not been checked by me.
2. Keep `.env`, `/etc/sesh/*.env` out of git (`.gitignore` already lists `.env`); rotate any secret ever pasted into chat or tickets.
3. Complete the restore drill and the real-printer checklist before real users.
