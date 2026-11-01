# Architecture decision log
| # | Date | Decision | Why / trade-off |
|---|---|---|---|
| 1 | 2026-10-31 | Build in phases, each delivered as a zip overlay for the VS Code project folder | User workflow |
| 2 | 2026-10-31 | Conversion: python-docx/openpyxl/Pillow/ReportLab, NO LibreOffice | Spec constraint. Fidelity is limited; go/no-go on real documents in Phase 1 |
| 3 | 2026-10-31 | PDF rasterising for previews/inspection uses pypdfium2 (Apache/BSD) | PyMuPDF is AGPL; avoided |
| 4 | 2026-10-31 | Printing: SumatraPDF CLI (GPLv3), not bundled; located via SUMATRA_PATH or poc\bin | Licence review needed before redistribution |
| 5 | 2026-10-31 | Printer discovery via PowerShell Get-Printer (no pywin32 dependency in PoC) | Fewer dependencies; revisit for agent |
| 6 | 2026-11-01 | Real printer testing deferred; app built first | No printer available now. Phase 1 print script is untested on Windows |
| 7 | 2026-11-01 | Modular monolith: Flask app factory + blueprints (auth, dashboard, admin) + services layer | Spec: no microservices |
| 8 | 2026-11-01 | SQLite default for local dev, PostgreSQL via DATABASE_URL (psycopg 3). Portable types only (sa.Uuid, VARCHAR enums, JSON) | Same migrations run on both; tests also run migrations |
| 9 | 2026-11-01 | Public ids are UUIDv4 columns; integer ids never exposed in URLs | Non-guessable identifiers |
| 10 | 2026-11-01 | One-office/one-agent/one-printer MVP enforced in services via config limits, not schema constraints | Multi-office later = config change, no migration |
| 11 | 2026-11-01 | Cross-company lookups return 404 (not 403) | Do not reveal that a record exists elsewhere |
| 12 | 2026-11-01 | Users are loaded on every request, so deactivation takes effect immediately | Cut-off without waiting for session expiry |
| 13 | 2026-11-01 | Unique login email across the system (lowercased) | Simple sign-in without a company selector; revisit for multi-company |
| 14 | 2026-11-01 | CSP allows only self + cdn.jsdelivr.net (Bootstrap CDN); no inline scripts | Vendor Bootstrap locally in Phase 7 hardening to remove the CDN dependency |
| 15 | 2026-11-01 | Agent secrets stored only as SHA-256 hashes (enrolment token, credential) | DB leak does not leak usable credentials |
| 16 | 2026-11-01 | Print-job and conversion tables exist now; their logic arrives in Phases 3-5 | Schema stable early; no inert UI shipped |
| 17 | 2026-11-01 | Conversion engine copied into `app/conversion/engine/` (from poc/), poc/ left untouched | App must not import from an experiment folder; duplication is accepted until Phase 7 cleanup |
| 18 | 2026-11-01 | Queue = database table (ConvertedFile status QUEUED), atomic claim by conditional UPDATE, polled by `flask run-worker`. Celery/Redis NOT used | Durable, no extra service, works on SQLite and PostgreSQL. Trade-off: polling latency (~2 s) and no built-in scheduling/priorities; a Celery adapter can wrap `process()` later |
| 19 | 2026-11-01 | Worker lease: PROCESSING older than CONVERSION_LEASE_SECONDS is failed as WORKER_LOST and retried; a slow worker cannot publish after losing its lease (conditional UPDATE) | Crash safety without duplicate results. No per-conversion hard timeout yet (would need a subprocess): Phase 7 |
| 20 | 2026-11-01 | Retries: retryable errors create a new ConvertedFile attempt up to MAX_CONVERSION_RETRIES (3 total); invalid/unsupported files fail immediately | No infinite retries; manual 'Try again' only when the last failure was retryable |
| 21 | 2026-11-01 | Files are served only through authorised Flask routes that stream from private storage; no presigned or public URLs | Spec: no permanent public URLs. Cost: PDF bytes pass through the web server |
| 22 | 2026-11-01 | Preview = server-rendered page PNGs (pypdfium2, first MAX_PREVIEW_PAGES pages) + Open/Download PDF; PDF.js not used | Mobile browsers cannot embed PDFs; no vendored JS or CSP/frame-header changes. Cost: CPU per page view, renders on demand (no cache yet) |
| 23 | 2026-11-01 | Upload validation runs twice (upload and worker): extension, MIME consistency, signature, ZIP structure/bomb limits, image header size, OLE/password-protected rejection | Spec: validate again before processing |
| 24 | 2026-11-01 | Visibility: admins see all company documents; workers see their own (or all if company policy `workers_can_view_all_documents`); only owner/admin may retry or remove; everything else returns 404 | Matches spec roles; policy flag already existed from Phase 2 |
| 25 | 2026-11-01 | Delete is soft (deleted_at); stored files are kept until a retention purge (not built yet) | Spec: retain records; retention job arrives in hardening |
