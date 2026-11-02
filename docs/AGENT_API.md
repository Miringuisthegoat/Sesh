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
