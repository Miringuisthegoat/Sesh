# Pilot checklist
Tick only what you actually saw happen. Items marked (HW) need real hardware or a server and could not be verified in development.

## A. Automated (on your PC)
- [ ] `python -m pytest -q` all green (record count: ____ passed, ____ skipped)
- [ ] `python -m flask db upgrade` on a fresh SQLite file and on a fresh PostgreSQL database; `python -m flask db check` clean
- [ ] `python -m scripts.perf_check` run on the production server; nothing flagged SLOW (note times: ______)
- [ ] `pip-audit -r requirements.txt` and `-r requirements-agent.txt` reviewed

## B. Server (HW)
- [ ] Deployed per `docs/DEPLOYMENT.md`; `https://<domain>/healthz` ok; HTTP redirects to HTTPS; certificate valid
- [ ] Company created by CLI before the site was public; admin password is strong and not shared
- [ ] `TRUSTED_PROXY_COUNT=1`, Redis rate limiting, `ENVIRONMENT=production`; a 6th wrong login in a minute from one IP gets 429 while another IP can still sign in
- [ ] R2 bucket private (opening an object URL without signing fails); upload a docx, preview and PDF download work
- [ ] `sesh-worker`, `sesh-web`, both timers active (`systemctl list-timers`); `flask purge-expired --dry-run` ran
- [ ] Backup ran, off-server copy exists, **restore drill done** on a scratch database (date: ______)

## C. Conversion quality (real documents)
- [ ] 5-10 documents the office really prints, each previewed and compared with the original; differences written down and accepted by the office (re-flowed Word layout, cached Excel values, no charts/text boxes)
- [ ] Upload page limits wording is understood by the staff who will use it

## D. Windows agent (HW, clean Windows 10 AND 11 x64, no Python installed)
- [ ] `.\build\build.ps1 -ServerUrl https://<domain>` then `.\build\smoke.ps1` passes
- [ ] SumatraPDF decision made (installed separately / bundled after licence review) and present on the office PC
- [ ] Installer runs without admin rights; shortcut and tray icon appear; starts at sign-in when ticked
- [ ] Wrong token shows a clear error; correct token connects; Office page shows Connected
- [ ] Real printer listed with correct status; select it; Office page shows it; a second printer is refused
- [ ] Unplug internet: amber, then recovers alone. Revoke on the Office page: red within ~30 s
- [ ] Upgrade over the installed version keeps settings; uninstall asks about data; both choices work
- [ ] `agent.log` contains no token, credential or document text

## E. Printing end to end (HW, real printer, worker on a different network)
- [ ] 1-page docx prints; multi-page xlsx prints; photo (png/jpg) prints
- [ ] Copies=2, A4/Letter, mono/colour (if supported), landscape each honoured (driver has the final say: note any exceptions)
- [ ] Printer switched off: job fails with a clear message (or waits) and "Submit again" works
- [ ] Kill the agent while printing: job ends "Check printer" (UNKNOWN), is NOT reprinted, admin can settle it
- [ ] Cancel a queued job; cancelling after "Ready to send" is refused
- [ ] Double-click Send creates one job; worker A cannot see worker B's documents or jobs
- [ ] "Sent to printer" wording is understood: staff know to check the printer

## F. People and process
- [ ] Admin and workers created, each told to keep passwords private and change the temporary one (Change password)
- [ ] Someone named to check "Needs attention" daily and the backup log weekly
- [ ] Rollback plan: stop agent, stop `sesh-web`, restore last backup
- [ ] Known limitations (`docs/SECURITY_REVIEW.md` table) read and accepted by the owner

Go/no-go: sections B, D and E must be fully ticked. Anything unticked is a written, dated, accepted risk.
