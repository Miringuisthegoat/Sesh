"""Discover printers already installed in Windows (USB, network, shared). Never installs or changes drivers.

Windows' status flags are only a hint: many printers/drivers report nothing useful when switched off or out of paper.
'AVAILABLE' therefore means "Windows reports no problem", not proof that paper will come out."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)

ATTR_WORK_OFFLINE = 0x400
OFFLINE_BITS = 0x80 | 0x1000 | 0x800000  # OFFLINE, NOT_AVAILABLE, SERVER_UNKNOWN
ERROR_BITS = (0x1 | 0x2 | 0x8 | 0x10 | 0x40 | 0x800 | 0x40000 | 0x100000 | 0x200000 | 0x400000)
# PAUSED, ERROR, PAPER_JAM, PAPER_OUT, PAPER_PROBLEM, OUTPUT_BIN_FULL, NO_TONER, USER_INTERVENTION, OUT_OF_MEMORY, DOOR_OPEN
VIRTUAL_MARKERS = ("print to pdf", "xps document writer", "onenote", "fax", "pdf")


@dataclass(frozen=True)
class InstalledPrinter:
    name: str
    status: str            # UNKNOWN | AVAILABLE | OFFLINE | ERROR (the values the server accepts)
    supports_color: bool | None = None
    is_virtual: bool = False
    port: str = ""


class PrinterBackend(Protocol):
    def list_printers(self) -> list[InstalledPrinter]: ...


def map_status(flags: int, attributes: int = 0) -> str:
    if attributes & ATTR_WORK_OFFLINE or flags & OFFLINE_BITS:
        return "OFFLINE"
    if flags & ERROR_BITS:
        return "ERROR"
    return "AVAILABLE"


def is_virtual(name: str) -> bool:
    n = name.lower()
    return any(m in n for m in VIRTUAL_MARKERS)


class WindowsPrinterBackend:
    def __init__(self):
        import win32print  # Windows only
        self._w = win32print
        self._color: dict[str, bool | None] = {}

    def _supports_color(self, name: str, port: str, status: str) -> bool | None:
        if name in self._color:
            return self._color[name]
        if status == "OFFLINE":  # querying a dead network printer can block; try again when it is back
            return None
        try:
            r = self._w.DeviceCapabilities(name, port, self._w.DC_COLORDEVICE)
            value = bool(r) if r in (0, 1) else None
        except Exception:
            value = None
        self._color[name] = value
        return value

    def list_printers(self) -> list[InstalledPrinter]:
        w = self._w
        flags = w.PRINTER_ENUM_LOCAL | w.PRINTER_ENUM_CONNECTIONS
        try:
            rows = w.EnumPrinters(flags, None, 2)
        except Exception:
            rows = w.EnumPrinters(flags, None, 4)  # names only (some shared connections)
        out = []
        for p in rows:
            name = p["pPrinterName"]
            port = p.get("pPortName") or ""
            status = map_status(p["Status"], p.get("Attributes", 0)) if "Status" in p else "UNKNOWN"
            out.append(InstalledPrinter(name, status, self._supports_color(name, port, status), is_virtual(name), port))
        return sorted(out, key=lambda x: x.name.lower())


class FakePrinterBackend:
    """For tests and non-Windows development (SESH_AGENT_FAKE_PRINTERS=1)."""
    def __init__(self, printers: list[InstalledPrinter] | None = None):
        self.printers = printers if printers is not None else [InstalledPrinter("Fake Office Printer", "AVAILABLE", True),
                                                               InstalledPrinter("Fake Spare Printer", "OFFLINE", False)]

    def list_printers(self) -> list[InstalledPrinter]:
        return list(self.printers)


def default_printer_backend() -> PrinterBackend:
    if os.environ.get("SESH_AGENT_FAKE_PRINTERS") == "1":
        return FakePrinterBackend()
    if os.name == "nt":
        return WindowsPrinterBackend()
    raise RuntimeError("Printer discovery needs Windows (or set SESH_AGENT_FAKE_PRINTERS=1 for development).")
