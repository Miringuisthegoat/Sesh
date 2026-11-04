# Production deployment (Ubuntu 22.04/24.04, single server)
Components: PostgreSQL, gunicorn (web), `flask run-worker` (conversions), Nginx + Let's Encrypt, private Cloudflare R2 bucket, optional Redis (shared rate limits). Config files are in `deploy/`.

## 1. Server and user
```bash
sudo apt update && sudo apt install -y python3 python3-venv postgresql nginx certbot python3-certbot-nginx redis-server rclone
sudo useradd --system --create-home --home-dir /opt/sesh --shell /usr/sbin/nologin sesh
```
Copy the project to `/opt/sesh` (owned by `sesh`), then:
```bash
cd /opt/sesh && sudo -u sesh python3 -m venv .venv
sudo -u sesh .venv/bin/pip install -r requirements.txt redis
```
(`redis` is only needed for `RATELIMIT_STORAGE_URI=redis://...`; pin the version you tested in your own requirements file.)

## 2. PostgreSQL
```bash
sudo -u postgres psql -c "CREATE USER sesh_user WITH PASSWORD 'CHANGE_ME';"
sudo -u postgres psql -c "CREATE DATABASE sesh OWNER sesh_user;"
```
## 3. Environment file `/etc/sesh/sesh.env` (mode 600, owner sesh) - never commit it
```
ENVIRONMENT=production
SECRET_KEY=<python -c "import secrets;print(secrets.token_urlsafe(48))">
DATABASE_URL=postgresql+psycopg://sesh_user:CHANGE_ME@localhost:5432/sesh
FLASK_APP=wsgi:app
TRUSTED_PROXY_COUNT=1                 # REQUIRED behind Nginx, or every user shares one IP and the login limit locks everybody out
RATELIMIT_STORAGE_URI=redis://localhost:6379/1   # required with more than one gunicorn worker, otherwise limits are per process
STORAGE_BACKEND=r2
R2_ENDPOINT_URL=https://<accountid>.r2.cloudflarestorage.com
R2_BUCKET=sesh-private
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
MAX_UPLOAD_MB=25                      # keep Nginx client_max_body_size in step (+1 MB)
```
Other settings and defaults: `.env.example`. The app refuses to start in production with the default `SECRET_KEY`.

## 4. Cloudflare R2
Create a **private** bucket (no public access, no custom public domain) and an API token limited to that bucket (Object Read & Write). Put the values above. Files are only ever served through logged-in, company-scoped routes.

## 5. Database schema and first company
```bash
cd /opt/sesh && sudo -u sesh bash -c 'set -a; . /etc/sesh/sesh.env; .venv/bin/python -m flask db upgrade'
sudo -u sesh bash -c 'set -a; . /etc/sesh/sesh.env; .venv/bin/python -m flask create-company'
```
Create the company **before** exposing the site: `/setup` is open to anyone until a company exists.

## 6. Services
```bash
sudo cp deploy/sesh-web.service deploy/sesh-worker.service deploy/sesh-maintenance.* deploy/sesh-backup.* /etc/systemd/system/
sudo mkdir -p /etc/sesh && sudo chmod 750 /etc/sesh
sudo systemctl daemon-reload
sudo systemctl enable --now sesh-web sesh-worker sesh-maintenance.timer sesh-backup.timer
```
Backup config (`/etc/sesh/backup.env`) is described in `docs/BACKUP_RESTORE.md`. Without the worker running, documents stay "Waiting to convert".

## 7. Nginx and HTTPS
Edit `deploy/nginx-sesh.conf` (domain, and the two `limit_req_zone` lines go in the `http {}` block), create `/etc/nginx/proxy_params_sesh` as shown at the bottom of that file, enable the site, then:
```bash
sudo certbot --nginx -d sesh.example.com
sudo nginx -t && sudo systemctl reload nginx
```
The app sets HSTS and Secure cookies when `ENVIRONMENT=production`, so serve it over HTTPS only.

## 8. Check
`curl -fsS https://sesh.example.com/healthz` returns `{"status":"ok"}`. Sign in, upload a docx, watch it convert. Then install the agent (`docs/AGENT.md`), build it with `.\build\build.ps1 -ServerUrl https://sesh.example.com`.

## Updates
```bash
cd /opt/sesh && sudo -u sesh git pull   # or copy the new overlay
sudo -u sesh .venv/bin/pip install -r requirements.txt
sudo -u sesh bash -c 'set -a; . /etc/sesh/sesh.env; .venv/bin/python -m flask db upgrade'
sudo systemctl restart sesh-web sesh-worker
```
Take a backup first (`sudo systemctl start sesh-backup`). A job queued during the restart is safe: the queue is the database.

## Daily maintenance
The timer runs `flask purge-expired` at 03:30 (see `docs/DECISIONS.md` 53-54). Try it by hand first: `... flask purge-expired --dry-run`.
