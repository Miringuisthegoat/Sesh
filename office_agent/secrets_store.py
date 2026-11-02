"""Credential protection with Windows DPAPI (current-user scope): only this Windows user on this PC can decrypt it."""
from __future__ import annotations

import base64
import logging
import os
from typing import Protocol

from .local_database import LocalDatabase

log = logging.getLogger(__name__)
_ENTROPY = b"sesh-agent-credential-v1"


class SecretStoreUnavailable(RuntimeError):
    pass


class Protector(Protocol):
    def protect(self, data: bytes) -> bytes: ...
    def unprotect(self, blob: bytes) -> bytes: ...


class DpapiProtector:
    def __init__(self):
        import win32crypt  # Windows only
        self._w = win32crypt

    def protect(self, data: bytes) -> bytes:
        return self._w.CryptProtectData(data, "Sesh Agent credential", _ENTROPY, None, None, 0)

    def unprotect(self, blob: bytes) -> bytes:
        return self._w.CryptUnprotectData(blob, _ENTROPY, None, None, 0)[1]


class InsecureDevProtector:
    """Development on non-Windows only (needs SESH_AGENT_ALLOW_INSECURE_STORE=1). NOT protection; never used on Windows."""
    def protect(self, data: bytes) -> bytes:
        return b"DEV" + data[::-1]

    def unprotect(self, blob: bytes) -> bytes:
        return blob[3:][::-1]


def default_protector() -> Protector:
    if os.name == "nt":
        return DpapiProtector()
    if os.environ.get("SESH_AGENT_ALLOW_INSECURE_STORE") == "1":
        log.warning("Using the INSECURE development credential store (not Windows).")
        return InsecureDevProtector()
    raise SecretStoreUnavailable("Windows DPAPI is required to store the agent credential.")


class CredentialStore:
    KEY = "credential"

    def __init__(self, db: LocalDatabase, protector: Protector):
        self._db, self._p = db, protector

    def set(self, credential: str) -> None:
        self._db.set(self.KEY, base64.b64encode(self._p.protect(credential.encode())).decode())

    def get(self) -> str | None:
        raw = self._db.get(self.KEY)
        if not raw:
            return None
        try:
            return self._p.unprotect(base64.b64decode(raw)).decode()
        except Exception:  # different Windows user/PC, or corrupt: the agent must be enrolled again
            log.warning("The stored credential could not be decrypted; enrolment is required again.")
            return None

    def clear(self) -> None:
        self._db.delete(self.KEY)
