"""Core controller: registration, heartbeat loop, printer selection. No UI and no Windows imports, so it is fully testable.

Responsibilities kept separate on purpose: cloud connectivity (State) vs printer availability (per printer status)."""
from __future__ import annotations

import logging
import socket
import threading
import time
from enum import Enum
from typing import Callable

from . import backoff
from .cloud_client import CloudClient, CloudError, Rejected, Unauthorized, Unavailable
from .config import (DEFAULT_HEARTBEAT_SECONDS, MAX_HEARTBEAT_SECONDS, MIN_HEARTBEAT_SECONDS, VERSION, normalize_server_url)
from .local_database import LocalDatabase
from .printer_manager import InstalledPrinter, PrinterBackend
from .secrets_store import CredentialStore

log = logging.getLogger(__name__)


class State(str, Enum):
    NOT_REGISTERED = "NOT_REGISTERED"
    CONNECTED = "CONNECTED"
    OFFLINE = "OFFLINE"        # registered, but the last contact with the server failed (or none yet)
    REVOKED = "REVOKED"        # the server no longer accepts this computer's credential


class AgentService:
    def __init__(self, db: LocalDatabase, store: CredentialStore, backend: PrinterBackend,
                 client_factory: Callable[[str, str | None], CloudClient], *, hostname: str | None = None, version: str = VERSION):
        self.db, self.store, self.backend, self._factory = db, store, backend, client_factory
        self.hostname, self.version = hostname or socket.gethostname(), version
        self._lock = threading.RLock()
        self._stop, self._wake = threading.Event(), threading.Event()
        self._listeners: list[Callable[[], None]] = []
        self._installed: list[InstalledPrinter] = []
        self.failures, self.last_ok, self.last_error = 0, None, None
        if db.get("revoked") == "1":
            self.state = State.REVOKED
        elif db.get("server_url") and store.get():
            self.state = State.OFFLINE
        else:
            self.state = State.NOT_REGISTERED

    # ---- observers / helpers ------------------------------------------------------------------------------------
    def add_listener(self, cb: Callable[[], None]) -> None:
        self._listeners.append(cb)

    def _notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                log.exception("listener failed")

    def _event(self, level: str, message: str) -> None:
        (log.warning if level != "info" else log.info)(message)
        self.db.log(level, message)

    def _client(self) -> CloudClient:
        cred, url = self.store.get(), self.db.get("server_url")
        if not cred or not url:
            raise Rejected("NOT_REGISTERED", "This computer is not connected to Sesh yet.")
        return self._factory(url, cred)

    def _mark_revoked(self) -> None:
        with self._lock:
            self.state = State.REVOKED
            self.db.set("revoked", "1")
            self.store.clear()
        self._event("error", "Sesh no longer accepts this computer. An administrator may have revoked it; enrol again.")
        self._notify()

    @property
    def paused(self) -> bool:
        return self.db.get("paused") == "1"

    def set_paused(self, paused: bool) -> None:
        """Persisted flag the Phase 5 job loop will honour (no jobs are taken in this version)."""
        self.db.set("paused", "1" if paused else "0")
        self._event("info", "Printing paused." if paused else "Printing resumed.")
        self._notify()

    # ---- registration -------------------------------------------------------------------------------------------
    def register(self, server_url: str, enrollment_token: str) -> None:
        url = normalize_server_url(server_url)
        token = (enrollment_token or "").strip()
        if not token:
            raise Rejected("BAD_REQUEST", "Enter the enrolment token from your Sesh administrator.")
        data = self._factory(url, None).register(token, self.hostname, self.version)
        with self._lock:
            self.store.set(data["credential"])
            self.db.set("server_url", url)
            self.db.set("agent_id", data.get("agent_id", ""))
            self.db.set("office", data.get("office", ""))
            self.db.set("heartbeat_seconds", data.get("heartbeat_seconds", DEFAULT_HEARTBEAT_SECONDS))
            self.db.delete("revoked")
            self.state, self.failures, self.last_error = State.OFFLINE, 0, None
        self._event("info", f"Registered with Sesh (office: {data.get('office', '')}).")
        saved = self.db.selected_printers()
        if saved:  # re-enrolment: restore the earlier choice if the server allows it
            try:
                self.set_enabled(saved)
            except CloudError as e:
                self.db.set_selected_printers([])
                self._event("warning", f"Previous printer choice was not restored: {e.message}")
        self._wake.set()
        self._notify()

    def unregister(self, force_local: bool = False) -> None:
        """Tell the server to revoke this agent, then forget everything local. force_local: forget even if the server is unreachable."""
        if self.state != State.NOT_REGISTERED:
            if self.state != State.REVOKED:
                try:
                    self._client().unregister()
                except Unauthorized:
                    pass  # already revoked on the server
                except CloudError:
                    if not force_local:
                        raise
        with self._lock:
            self.store.clear()
            self.db.delete("agent_id", "office", "heartbeat_seconds", "revoked", "paused")
            self.db.set_selected_printers([])
            self.state, self.failures, self.last_error, self.last_ok = State.NOT_REGISTERED, 0, None, None
        self._event("info", "This computer was disconnected from Sesh.")
        self._notify()

    def rotate_credential(self) -> None:
        """Replace the credential. Stored immediately; if the response is lost the admin must re-enrol (documented)."""
        new = self._client().rotate()
        self.store.set(new)
        self._event("info", "Credential rotated.")

    # ---- printers -----------------------------------------------------------------------------------------------
    def refresh_printers(self) -> list[InstalledPrinter]:
        try:
            self._installed = self.backend.list_printers()
        except Exception:
            log.exception("could not list Windows printers")
        return list(self._installed)

    def _payload(self) -> list[dict]:
        return [{"windows_name": p.name, "status": p.status, "supports_color": p.supports_color} for p in self._installed]

    def printers_view(self) -> list[dict]:
        enabled = set(self.db.selected_printers())
        view = [{"name": p.name, "status": p.status, "supports_color": p.supports_color, "virtual": p.is_virtual,
                 "enabled": p.name in enabled, "installed": True} for p in self._installed]
        known = {p.name for p in self._installed}
        view += [{"name": n, "status": "OFFLINE", "supports_color": None, "virtual": False, "enabled": True, "installed": False}
                 for n in sorted(enabled - known)]  # selected earlier but no longer installed in Windows
        return view

    def set_enabled(self, names: list[str]) -> list[str]:
        """Ask the server to make exactly these printers available. Local choice changes only if the server accepts."""
        self.refresh_printers()
        try:
            res = self._client().select_printers(list(dict.fromkeys(names)), self._payload())
        except Unauthorized:
            self._mark_revoked()
            raise
        active = [p["windows_name"] for p in res.get("printers", [])]
        self.db.set_selected_printers(active)
        self._event("info", "Printers available to Sesh: " + (", ".join(active) if active else "none"))
        self._notify()
        return active

    # ---- heartbeat loop -----------------------------------------------------------------------------------------
    def tick(self) -> float:
        """One heartbeat attempt. Returns seconds to wait before the next one."""
        if self.state in (State.NOT_REGISTERED, State.REVOKED):
            return 1.0
        try:
            self.refresh_printers()
            data = self._client().heartbeat(self.version, self._payload())
        except Unauthorized:
            self._mark_revoked()
            return 1.0
        except CloudError as e:
            return self._failed(e.message)
        except Exception:
            log.exception("unexpected heartbeat error")
            return self._failed("Unexpected error; see the log.")
        was = self.state
        with self._lock:
            self.failures, self.last_error, self.last_ok, self.state = 0, None, time.time(), State.CONNECTED
        if was != State.CONNECTED:
            self._event("info", "Connected to Sesh.")
        server_active = [p["windows_name"] for p in data.get("printers", [])]
        if sorted(server_active) != sorted(self.db.selected_printers()):  # the server is the authority on what is enabled
            self.db.set_selected_printers(server_active)
        hb = data.get("heartbeat_seconds")
        if isinstance(hb, int):
            self.db.set("heartbeat_seconds", max(MIN_HEARTBEAT_SECONDS, min(MAX_HEARTBEAT_SECONDS, hb)))
        self._notify()
        return float(self.db.get("heartbeat_seconds", str(DEFAULT_HEARTBEAT_SECONDS)))

    def _failed(self, message: str) -> float:
        with self._lock:
            first = self.state != State.OFFLINE or self.failures == 0
            self.failures += 1
            self.last_error, self.state = message, State.OFFLINE
        if first:
            self._event("warning", f"Not connected to Sesh: {message}")
        self._notify()
        return backoff.delay(self.failures)

    def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                wait = self.tick()
            except Exception:  # never let the loop die
                log.exception("heartbeat loop error")
                wait = 30.0
            self._wake.wait(wait)
            self._wake.clear()

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    # ---- presentation helpers -----------------------------------------------------------------------------------
    def describe(self) -> str:
        if self.state == State.NOT_REGISTERED:
            return "Not connected to Sesh yet"
        if self.state == State.REVOKED:
            return "This computer was removed from Sesh"
        if self.state == State.CONNECTED:
            return "Connected to Sesh" + (" (printing paused)" if self.paused else "")
        return "Connecting to Sesh…" if self.last_ok is None and not self.last_error else f"Not connected: {self.last_error}. Retrying."

    def snapshot(self) -> dict:
        return {"state": self.state.value, "text": self.describe(), "office": self.db.get("office"), "server": self.db.get("server_url"),
                "paused": self.paused, "last_ok": self.last_ok, "printers": self.printers_view(), "activity": self.db.recent_activity(10)}
