"""Paths, constants and server-URL rules. No secrets here."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

APP_NAME = "Sesh Agent"
VERSION = "0.1.0"
MUTEX_NAME = "SeshAgentSingleInstance"  # must match AppMutex in build/installer.iss
DEFAULT_HEARTBEAT_SECONDS = 30
MIN_HEARTBEAT_SECONDS, MAX_HEARTBEAT_SECONDS = 10, 300
MAX_ACTIVITY_ROWS = 200
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def data_dir() -> Path:
    """Per-user data (database, logs). Kept on upgrade; removed on uninstall only if the user agrees."""
    override = os.environ.get("SESH_AGENT_HOME")
    if override:
        p = Path(override)
    elif os.name == "nt":
        p = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "SeshAgent"
    else:
        p = Path.home() / ".sesh-agent"
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_dir() -> Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def app_dir() -> Path:
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent


def default_server_url() -> str:
    """SESH_SERVER_URL, else the first line of server_url.txt next to the program (written at build time), else ''."""
    env = os.environ.get("SESH_SERVER_URL", "").strip()
    if env:
        return env
    f = app_dir() / "server_url.txt"
    try:
        return f.read_text(encoding="utf-8").splitlines()[0].strip() if f.is_file() else ""
    except (OSError, IndexError):
        return ""


def normalize_server_url(raw: str) -> str:
    """HTTPS only (plain HTTP only for localhost development). Raises ValueError with a user-safe message."""
    raw = (raw or "").strip()
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower()
    except ValueError:
        raise ValueError("That server address is not valid.") from None
    if not host or parts.scheme not in ("http", "https"):
        raise ValueError("Enter the full server address, for example https://sesh.example.com")
    if parts.scheme == "http" and host not in LOCAL_HOSTS:
        raise ValueError("The server address must start with https://")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("The server address must not contain a login, query or fragment.")
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"
