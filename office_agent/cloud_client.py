"""HTTPS client for the Sesh agent API (see docs/AGENT_API.md). Outbound only; the credential never appears in errors/logs."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .config import VERSION, normalize_server_url

API = "/api/agent/v1"


class CloudError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class Unauthorized(CloudError):
    """Credential unknown or revoked."""


class Rejected(CloudError):
    """The server understood the request and said no (bad input, limit reached, bad enrolment token)."""


class Unavailable(CloudError):
    """Network trouble, redirect, 429 or 5xx. Retry later with backoff."""


class ChecksumMismatch(CloudError):
    """The downloaded bytes do not match the checksum/size the server announced. The file must not be printed."""


class CloudClient:
    def __init__(self, base_url: str, credential: str | None = None, session=None, timeout=(5, 20)):
        self.base = normalize_server_url(base_url) + API
        self._credential, self._session, self._timeout = credential, session, timeout

    def _sess(self):
        if self._session is None:
            import requests
            self._session = requests.Session()
        return self._session

    def _call(self, method: str, path: str, body: dict | None = None, auth: bool = True) -> dict:
        import requests
        headers = {"User-Agent": f"SeshAgent/{VERSION}", "Accept": "application/json"}
        if auth:
            headers["Authorization"] = "Bearer " + (self._credential or "")
        try:
            r = self._sess().request(method, self.base + path, json=body, headers=headers, timeout=self._timeout,
                                     allow_redirects=False)  # never forward the credential to a redirect target
        except requests.RequestException as e:
            raise Unavailable("NETWORK", "Cannot reach the Sesh server. Check the internet connection.") from e
        status = r.status_code
        if 300 <= status < 400:
            raise Unavailable("REDIRECT", "The server address redirected the agent. Check the address (use https://).", status)
        try:
            data = r.json()
        except ValueError:
            data = None
        if status >= 400:
            err = data.get("error", {}) if isinstance(data, dict) and isinstance(data.get("error"), dict) else {}
            code, msg = str(err.get("code", "ERROR")), str(err.get("message", f"The server returned an error ({status})."))[:300]
            if status == 401 and auth:
                raise Unauthorized(code, msg, status)
            if status == 429 or status >= 500:
                raise Unavailable(code, msg, status)
            raise Rejected(code, msg, status)
        if not isinstance(data, dict):
            raise Unavailable("BAD_RESPONSE", "The server sent an unexpected response.", status)
        return data

    def register(self, enrollment_token: str, hostname: str, version: str) -> dict:
        data = self._call("POST", "/register", {"enrollment_token": enrollment_token, "hostname": hostname, "agent_version": version}, auth=False)
        if not isinstance(data.get("credential"), str):
            raise Unavailable("BAD_RESPONSE", "The server sent an unexpected response.")
        return data

    def heartbeat(self, version: str, printers: list[dict]) -> dict:
        return self._call("POST", "/heartbeat", {"agent_version": version, "printers": printers})

    def select_printers(self, enabled: list[str], installed: list[dict]) -> dict:
        return self._call("PUT", "/printers", {"enabled": enabled, "installed": installed})

    def rotate(self) -> str:
        return self._call("POST", "/credential/rotate")["credential"]

    def unregister(self) -> None:
        self._call("POST", "/unregister")

    # ---- print jobs (Phase 5b) ---------------------------------------------------------------------------------
    def claim_job(self) -> dict | None:
        job = self._call("POST", "/jobs/claim").get("job")
        return job if isinstance(job, dict) else None

    def report_status(self, job_id: str, claim_token: str, status: str, code: str | None = None, message: str | None = None) -> None:
        self._call("POST", f"/jobs/{job_id}/status", {"claim_token": claim_token, "status": status, "error_code": code, "error_message": message})

    def renew_lease(self, job_id: str, claim_token: str) -> None:
        self._call("POST", f"/jobs/{job_id}/lease", {"claim_token": claim_token})

    def download_pdf(self, job_id: str, claim_token: str, dest: Path, expected_sha256: str, expected_size: int | None,
                     max_bytes: int) -> None:
        """Stream the job's PDF to `dest`, hashing as it arrives. Raises ChecksumMismatch (file deleted) if bytes differ."""
        import requests
        headers = {"User-Agent": f"SeshAgent/{VERSION}", "Authorization": "Bearer " + (self._credential or ""), "X-Claim-Token": claim_token}
        try:
            r = self._sess().request("GET", f"{self.base}/jobs/{job_id}/pdf", headers=headers, timeout=(5, 60),
                                     allow_redirects=False, stream=True)
        except requests.RequestException as e:
            raise Unavailable("NETWORK", "Cannot reach the Sesh server. Check the internet connection.") from e
        try:
            status = r.status_code
            if 300 <= status < 400:
                raise Unavailable("REDIRECT", "The server address redirected the agent.", status)
            if status != 200:
                try:
                    data = r.json()
                except ValueError:
                    data = None
                err = data.get("error", {}) if isinstance(data, dict) and isinstance(data.get("error"), dict) else {}
                code, msg = str(err.get("code", "ERROR")), str(err.get("message", f"The server returned an error ({status})."))[:300]
                if status == 401:
                    raise Unauthorized(code, msg, status)
                if status == 429 or status >= 500:
                    raise Unavailable(code, msg, status)
                raise Rejected(code, msg, status)
            digest, size = hashlib.sha256(), 0
            try:
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(65536):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > max_bytes:
                            raise ChecksumMismatch("TOO_LARGE", "The PDF is larger than this computer accepts.")
                        digest.update(chunk)
                        f.write(chunk)
            except requests.RequestException as e:
                Path(dest).unlink(missing_ok=True)
                raise Unavailable("NETWORK", "The download was interrupted.") from e
            except ChecksumMismatch:
                Path(dest).unlink(missing_ok=True)
                raise
        finally:
            getattr(r, "close", lambda: None)()
        if digest.hexdigest() != expected_sha256.lower() or (expected_size is not None and size != expected_size):
            Path(dest).unlink(missing_ok=True)
            raise ChecksumMismatch("CHECKSUM", "The downloaded PDF does not match its checksum.")
