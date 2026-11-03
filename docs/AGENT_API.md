# Sesh agent API v1 (Phase 4a)
Base path `/api/agent/v1`. JSON only. HTTPS outside local development. Errors: `{"error": {"code": "...", "message": "..."}}`.
Authenticated calls send `Authorization: Bearer <credential>`; any failure is `401 UNAUTHORIZED` (revoked, unknown, or company/office inactive).

| Call | Auth | Purpose |
|---|---|---|
| `POST /register` `{enrollment_token, hostname, agent_version}` | none (rate limited) | Single-use token -> `201 {agent_id, credential, office, heartbeat_seconds}`. Credential is shown once. Bad/expired/used token: `401 INVALID_ENROLLMENT`. |
| `POST /heartbeat` `{agent_version?, printers?: [{windows_name, status, supports_color?}]}` | bearer | Updates last_seen. `status` is UNKNOWN/AVAILABLE/OFFLINE/ERROR. If `printers` is sent, active printers missing from it become OFFLINE; if omitted, availability is unchanged. Returns `{server_time, heartbeat_seconds, printers: [active printers]}`. |
| `PUT /printers` `{enabled: [names], installed: [{windows_name, status, supports_color?}]}` | bearer | Select which installed printers Sesh may use. `409 SELECTION_REJECTED` if over the account limit (MVP: 1) or a name is not in `installed`; nothing changes on rejection. |
| `POST /credential/rotate` | bearer | New credential returned once; the old one stops immediately. |
| `POST /unregister` | bearer | Agent revokes itself. |

Not in 4a (arrives with Phase 5): job claim, PDF download, job status updates.
Admin side: `/admin/office` shows agent state, "Generate token" (one agent per office in the MVP) and "Revoke".
Known gaps: AgentEvent rows are not pruned yet; an expired, never-used enrolment keeps its slot until an admin revokes it.


## Print jobs (Phase 5b)
All bearer-authenticated like the calls above. A job is identified by its public id; the **claim token** returned by `claim` proves this agent holds it (only its hash is stored server-side).

| Call | Purpose |
|---|---|
| `POST /jobs/claim` | Atomically take the oldest queued job for this office/printers. `{job: null}` if none, else `{job: {id, claim_token, printer, copies, paper_size, color, landscape, sha256, size_bytes, pages, lease_seconds}}`. The token is shown once. |
| `GET /jobs/<id>/pdf` + header `X-Claim-Token` | Streams the PDF (`X-Content-SHA256` header). Only while the agent holds the job (CLAIMED/DOWNLOADING/DOWNLOADED); renews the lease. |
| `POST /jobs/<id>/status` `{claim_token, status, error_code?, error_message?}` | Report DOWNLOADING, DOWNLOADED, SUBMITTED_TO_PRINTER, COMPLETED, FAILED, UNKNOWN, or QUEUED (hand the job back). Repeating the current status is a no-op. Each report renews the lease. |
| `POST /jobs/<id>/lease` `{claim_token}` | Renew the lease without changing status. |

Errors: `404 JOB_NOT_FOUND` (unknown, not held, wrong token, or already handed back), `409 JOB_CANCELLED` / `ILLEGAL_TRANSITION` / `JOB_NOT_AVAILABLE` (body includes `job_status`). On 404 or 409 the agent must stop working on that job and never print it.
Lease: if the agent stops reporting for `JOB_LEASE_SECONDS`, CLAIMED/DOWNLOADING jobs are requeued, DOWNLOADED jobs become UNKNOWN (never reprinted automatically).
