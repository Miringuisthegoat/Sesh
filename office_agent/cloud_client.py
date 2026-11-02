"""HTTPS client for the Sesh agent API (see docs/AGENT_API.md). Outbound only; the credential never appears in errors/logs."""
from __future__ import annotations

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
