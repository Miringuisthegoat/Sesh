# Sesh Agent (Windows) - Phase 4b
Tray application in the signed-in user's session. Outbound HTTPS only. API contract: `docs/AGENT_API.md`.

## What it does now
First launch -> enter server address + enrolment token -> registered -> choose printers -> heartbeat every ~30 s (reports every installed printer's Windows status; the server marks agent connectivity and printer availability separately). Survives restarts and internet loss (exponential backoff with jitter). Tray: status, selected printers, refresh, pause/resume, settings window, logs, quit. Settings window: choose printers, unregister.

## Not in this phase (arrives after Phase 5 server endpoints)
Job claim, PDF download + checksum, printing (SumatraPDF), job-recovery logic. The local `jobs` table already exists so upgrades keep it. **Pause/Resume only sets a saved flag** the job loop will honour; it changes nothing yet. SumatraPDF is NOT bundled (licence review still open, decision 4).
Credential rotation exists in code (`AgentService.rotate_credential`) but nothing triggers it yet.

## Develop / test without Windows hardware
```powershell
pip install -r requirements-agent.txt
python -m pytest tests/test_agent_core.py tests/test_agent_against_backend.py -q
$env:SESH_AGENT_FAKE_PRINTERS="1"; $env:SESH_SERVER_URL="http://127.0.0.1:5000"; python -m office_agent   # real Windows printers if the variable is not set
```
(Non-Windows only: `SESH_AGENT_ALLOW_INSECURE_STORE=1` allows a NON-secure credential store for development.)

## Build the installer (Windows 10/11 x64, Inno Setup 6 installed)
```powershell
.\build\build.ps1 -ServerUrl https://your-sesh-server
```
Output: `dist\installer\SeshAgentSetup.exe`. Per-user install (no admin rights), Start Menu shortcut, optional desktop shortcut, optional start-at-sign-in (HKCU Run key). The same installer serves every account type; limits are enforced by the server.

## Data and uninstall
`%LOCALAPPDATA%\SeshAgent\` holds `agent.db` (settings, selected printers, activity, job-recovery table; the credential is DPAPI-encrypted and only this Windows user on this PC can decrypt it) and `logs\agent.log` (rotated, secrets redacted, no document contents). Upgrades never touch it. Uninstall removes the program; it asks whether to delete this folder (default: keep).
If the stored credential cannot be decrypted (different Windows user/PC), the agent asks for a new enrolment.

## Clean-machine checklist (NOT yet done - needs a clean Windows 10 and 11 VM)
1. Install without Python present; shortcut works; icon appears near the clock.
2. First launch opens setup; wrong token shows a clear error; a good token connects (status green; Office page shows "Connected").
3. Printer list shows your real USB/network printers; select one -> Office page shows it; a second is refused with the server's message.
4. Switch the printer off/unplug: status changes within ~30 s (Windows may not report it for every model - note which).
5. Disconnect the internet: icon turns amber, recovers by itself. Revoke on the Office page: the agent turns red within ~30 s and asks for a new token.
6. Sign out/in with "start automatically" ticked: agent starts. Run installer again over itself: data kept. Uninstall: both choices of the data prompt behave.
7. `agent.log` contains no token/credential.
