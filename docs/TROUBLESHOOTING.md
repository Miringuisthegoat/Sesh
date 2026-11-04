# Troubleshooting
Logs: web `journalctl -u sesh-web`, worker `journalctl -u sesh-worker`, Nginx `/var/log/nginx/error.log`, agent `%LOCALAPPDATA%\SeshAgent\logs\agent.log` (tray menu: View logs). Logs never contain document contents or secrets.

| Symptom | Likely cause | Fix |
|---|---|---|
| Document stays "Waiting to convert" | Worker not running | `systemctl status sesh-worker`; `--once` locally: `flask run-worker --once` |
| Document "Failed", message about password/old format | Password-protected or `.doc`/`.xls` file | Save as plain `.docx`/`.xlsx` and upload again |
| Conversion Failed `INTERNAL_ERROR` after 3 tries | Bug or resource limit; see worker log; `MemoryMax` kill shows as WORKER_LOST first | Check log; try a smaller file; report with the document type |
| Everyone gets "Too many attempts" (429) | `TRUSTED_PROXY_COUNT` is 0 behind Nginx, so all users share one IP | Set `TRUSTED_PROXY_COUNT=1`, restart web |
| "The page may have expired" (400) | CSRF token expired (session ended) or cookies blocked | Reload the page and retry |
| Cannot sign in over HTTPS but works locally | Secure cookies need HTTPS and correct `X-Forwarded-Proto` | Use `proxy_params_sesh` from `deploy/nginx-sesh.conf` |
| Upload rejected, 413 | File over `MAX_UPLOAD_MB` or Nginx `client_max_body_size` | Raise both together |
| 502 from Nginx | gunicorn down or crashed | `systemctl status sesh-web`; `curl 127.0.0.1:8000/healthz` |
| `/healthz` returns 503 | Database unreachable | `systemctl status postgresql`; check `DATABASE_URL` |
| `flask db upgrade` fails on `purged_at` exists | Migration was applied twice by hand | `flask db stamp head` after checking the column exists |
| Agent "Not connected" (amber) | No internet, wrong address, or server down | Open the server address in a browser from that PC; check `agent.log` |
| Agent log: "redirected the agent" | Server address uses `http://` or a redirecting domain | Use the exact `https://` address, no trailing path |
| Agent red "removed from Sesh" | Revoked by an admin, or credential unreadable (different Windows user/PC) | Admin: Office page, Generate token; agent: enter new token |
| Enrolment token "invalid, expired or already used" | Single use, 24 h | Generate a new one (an expired unused one no longer blocks it) |
| Second printer refused | MVP limit: one printer | Untick the first, save, then tick the other |
| Agent takes no jobs; log says SumatraPDF not found | Not bundled (licence review open) | Install it, or put `SumatraPDF.exe` next to `SeshAgent.exe`, or set `SUMATRA_PATH` |
| Job FAILED `PRINTER_UNAVAILABLE` | Printer removed, unticked, or Windows reports it offline | Fix in Windows / agent window; "Submit again" |
| Job FAILED `CHECKSUM_MISMATCH` | PDF damaged in transit or changed in storage | Submit again; if repeated, check storage and the worker log |
| Job "Check printer" (UNKNOWN) | Agent/PC stopped at the wrong moment, or print command timed out | Look at the printer, then admin presses "It printed" / "It did not print". Sesh never reprints by itself |
| Job "Sent to printer" but no paper | Windows queue paused, printer off, no paper, driver issue | Check the Windows print queue on the office PC; status cannot prove paper came out |
| Job stays "Waiting" | Agent offline, paused, or no printer ticked | Tray icon: resume printing; Office page shows connection and printer separately |
| Disk filling up on the server | Purge not running | `systemctl list-timers`, `flask purge-expired --dry-run`; also check `/var/backups/sesh` |
