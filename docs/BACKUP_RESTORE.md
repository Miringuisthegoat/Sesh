# Backup and restore
What must be backed up: (1) the PostgreSQL database, (2) the R2 bucket (originals and PDFs), (3) `/etc/sesh/sesh.env` (keep a copy in a password manager: without `SECRET_KEY` nothing breaks except sessions, but the R2/DB credentials are needed to restore).
The database alone is not enough: rows point at stored files. A restored database with missing files shows "file not found" for those documents.

## Database (automatic)
`deploy/backup.sh` runs nightly (02:15) via `sesh-backup.timer`. It writes `sesh-<UTC time>.dump` (+ `.sha256`), checks it with `pg_restore --list`, keeps `KEEP_DAYS` days, and can copy to a second place with rclone.
`/etc/sesh/backup.env` (mode 600):
```
BACKUP_PG_URL=postgresql://sesh_user:CHANGE_ME@localhost:5432/sesh
BACKUP_DIR=/var/backups/sesh
KEEP_DAYS=14
RCLONE_REMOTE=            # optional off-server copy, e.g. offsite:sesh-db-backups
```
Run once by hand: `sudo systemctl start sesh-backup && journalctl -u sesh-backup -n 20`. **A backup on the same disk is not a backup**: set `RCLONE_REMOTE` (another provider or bucket) or copy the folder off the server.

## Files (R2)
Copy the bucket to a second bucket/provider on a schedule, for example `rclone sync r2:sesh-private offsite:sesh-private-copy` (configure both remotes with `rclone config`). Check Cloudflare's current documentation for any built-in versioning/replication before relying on it. Do not use `sync` in the direction of an empty bucket.
The retention purge deletes files on purpose; a backup copy of the bucket will keep them until you expire the copy: align the copy's lifetime with your retention promise.

## Restore drill (do this once before the pilot, then every few months)
On a spare machine or a scratch database, never first on production:
```bash
createdb -O sesh_user sesh_restore
pg_restore --no-owner --role=sesh_user -d sesh_restore /var/backups/sesh/sesh-XXXX.dump
DATABASE_URL=postgresql+psycopg://sesh_user:CHANGE_ME@localhost/sesh_restore python -m flask db current   # shows the migration head
```
Start the app against it (`DATABASE_URL=... STORAGE_BACKEND=r2 ...`), sign in, open a document and its PDF preview, open a print job. Record the date and result in the pilot checklist.

## Disaster restore
1. New server: follow `docs/DEPLOYMENT.md` steps 1-4 (same `sesh.env`, same R2 bucket).
2. `createdb` + `pg_restore` the newest good dump into `sesh`.
3. `flask db upgrade` (applies migrations newer than the dump).
4. Start the services. Agents reconnect by themselves (they only need the server address). If the dump is older than an agent enrolment/credential rotation, the agent is rejected: revoke and re-enrol it on the Office page.
5. Jobs that were mid-flight at the time of loss: check "Needs attention" and resolve uncertain ones by looking at the printer. Never assume they printed.
