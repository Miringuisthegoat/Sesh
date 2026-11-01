# Sesh: remote document conversion & printing (MVP)

Status: Phase 3 (document workflow). Working now: setup, sign-in, roles, worker management, company settings, office setup, audit log, uploads, private storage, background PDF conversion, PDF preview/download.
Not built yet (so not shown as working): Windows agent, print jobs. `poc/` holds the Phase 1 conversion/print experiment.

## Local development (Windows PowerShell, VS Code)
```powershell
cd C:\Users\user\Projects\Sesh
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env        # then edit SECRET_KEY
python -m flask db upgrade         # creates instance\sesh.db (SQLite) by default
python -m flask create-company     # or open /setup in the browser on first run
python -m flask run                # http://127.0.0.1:5000
# in a SECOND terminal (venv active): conversions only happen while the worker runs
python -m flask run-worker         # add --once to process the queue and exit
python -m pytest -q                # run the tests
```
Debug mode (auto-reload, dev only): `$env:FLASK_DEBUG="1"; python -m flask run`

## Documents and conversion
Upload -> stored privately (`storage/` locally, R2 in production) -> queued -> `flask run-worker` converts -> PDF stored privately. Files are only served through logged-in, company-scoped routes. If a document stays "Waiting to convert", the worker is not running.
R2: create a PRIVATE bucket and an API token limited to it, then set `STORAGE_BACKEND=r2` and the four `R2_*` values in `.env`. Optional storage tests against a mock S3: `pip install -r requirements-dev.txt`.
Conversion limits are listed on the upload page and in `poc/README.md` (re-flowed Word layout, cached Excel values, no charts/text boxes).

## PostgreSQL
```sql
-- psql as postgres superuser
CREATE USER sesh_user WITH PASSWORD 'CHANGE_ME';
CREATE DATABASE sesh OWNER sesh_user;
```
Then in `.env`: `DATABASE_URL=postgresql+psycopg://sesh_user:CHANGE_ME@localhost:5432/sesh` and run `python -m flask db upgrade`.

## Migrations
After changing models: `python -m flask db migrate -m "describe change"`, REVIEW the generated file in `migrations\versions`, then `python -m flask db upgrade`. `python -m flask db check` fails if models and migrations differ (also covered by a test).

## Layout
`app/models` data model · `app/services` business rules (audit, users, offices, tenant scoping) · `app/auth`, `app/admin`, `app/dashboard`, `app/documents` blueprints · `app/conversion` validation + converters · `app/services/conversion_queue.py` worker logic · `tests/` · `docs/DECISIONS.md` decision log.

## Security notes (current)
Scrypt password hashes · CSRF on every POST · HttpOnly + SameSite=Lax cookies (Secure in production) · login/setup rate limit (memory store in dev; set RATELIMIT_STORAGE_URI to Redis in production) · server-side role checks · every company-owned lookup filtered by company_id · audit log for sign-ins and admin changes (never passwords) · production refuses to start with the default SECRET_KEY.
Known gaps (later phases): Bootstrap loads from a CDN; no account lockout beyond rate limiting; no password-change page for workers yet (admins reset passwords); no email-based reset.
