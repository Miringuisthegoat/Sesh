"""Tk windows (setup / settings). Tk owns the main thread; network work runs in worker threads and posts results back."""
from __future__ import annotations

import logging
import os
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .agent_service import AgentService, State
from .cloud_client import CloudError
from .config import default_server_url, log_dir
from .tray_interface import TrayApp

log = logging.getLogger(__name__)
STATUS_TEXT = {"AVAILABLE": "ready", "OFFLINE": "offline", "ERROR": "needs attention", "UNKNOWN": "status unknown"}


def explain(exc: Exception) -> str:
    if isinstance(exc, CloudError):
        return exc.message
    if isinstance(exc, ValueError):
        return str(exc)
    log.error("unexpected UI error: %s", type(exc).__name__, exc_info=exc)
    return "Something went wrong. See the log for details."


class MainWindow(tk.Toplevel):
    def __init__(self, ui: "Ui"):
        super().__init__(ui.root)
        self.ui, self.svc = ui, ui.service
        self.title("Sesh Agent")
        self.geometry("540x500")
        self.minsize(480, 420)
        self.body = ttk.Frame(self, padding=16)
        self.body.pack(fill="both", expand=True)
        self._mode = None
        self.render()

    def _wanted_mode(self) -> str:
        return "setup" if self.svc.state in (State.NOT_REGISTERED, State.REVOKED) else "main"

    def render(self) -> None:
        if not self.winfo_exists():
            return
        mode = self._wanted_mode()
        if mode != self._mode:
            for w in self.body.winfo_children():
                w.destroy()
            self._mode = mode
            (self._build_setup if mode == "setup" else self._build_main)()
        elif mode == "main":
            self.status.set(self.svc.describe())

    # ---- first launch / re-enrolment --------------------------------------------------------------------------
    def _build_setup(self) -> None:
        b = self.body
        ttk.Label(b, text="Connect this computer to Sesh", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        note = ("This computer was removed from Sesh. Enter a new enrolment token to reconnect." if self.svc.state == State.REVOKED
                else "Ask your Sesh administrator for an enrolment token (Admin → Office → Generate token).")
        ttk.Label(b, text=note, wraplength=480).pack(anchor="w", pady=(4, 12))
        self.url = tk.StringVar(value=self.svc.db.get("server_url") or default_server_url())
        self.token = tk.StringVar()
        ttk.Label(b, text="Sesh server address").pack(anchor="w")
        ttk.Entry(b, textvariable=self.url).pack(fill="x", pady=(0, 8))
        ttk.Label(b, text="Enrolment token").pack(anchor="w")
        ttk.Entry(b, textvariable=self.token, show="•").pack(fill="x", pady=(0, 12))
        self.connect_btn = ttk.Button(b, text="Connect", command=self._connect)
        self.connect_btn.pack(anchor="w")
        self.msg = ttk.Label(b, text="", foreground="#b00020", wraplength=480)
        self.msg.pack(anchor="w", pady=8)

    def _connect(self) -> None:
        self.connect_btn.state(["disabled"])
        self.msg.config(text="Connecting…", foreground="#444")

        def done(_, exc):
            if not self.winfo_exists():
                return
            if exc:
                self.connect_btn.state(["!disabled"])
                self.msg.config(text=explain(exc), foreground="#b00020")
            else:
                self.render()
        self.ui.run_bg(lambda: self.svc.register(self.url.get(), self.token.get()), done)

    # ---- settings / printer choice ----------------------------------------------------------------------------
    def _build_main(self) -> None:
        b = self.body
        self.status = tk.StringVar(value=self.svc.describe())
        ttk.Label(b, textvariable=self.status, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        office = self.svc.db.get("office")
        if office:
            ttk.Label(b, text=f"Office: {office}").pack(anchor="w")
        ttk.Label(b, text="Printers Sesh may use", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(12, 2))
        ttk.Label(b, text="Tick the printer(s) to make available. Windows' status is only a hint: it cannot prove the "
                          "printer is on or has paper.", wraplength=490, foreground="#555").pack(anchor="w")
        self.plist = ttk.Frame(b)
        self.plist.pack(fill="x", pady=6)
        self.msg = ttk.Label(b, text="", wraplength=490)
        self.msg.pack(anchor="w", pady=4)
        row = ttk.Frame(b)
        row.pack(anchor="w", pady=4)
        ttk.Button(row, text="Save selection", command=self._save).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="Refresh printers", command=self._refresh).pack(side="left", padx=(0, 6))
        self.pause_btn = ttk.Button(row, text="", command=self._toggle_pause)
        self.pause_btn.pack(side="left")
        row2 = ttk.Frame(b)
        row2.pack(anchor="w", pady=4)
        ttk.Button(row2, text="View logs", command=self.ui.open_logs).pack(side="left", padx=(0, 6))
        ttk.Button(row2, text="Unregister this computer…", command=self._unregister).pack(side="left")
        self._fill_printers()

    def _fill_printers(self) -> None:
        for w in self.plist.winfo_children():
            w.destroy()
        self.vars: dict[str, tk.BooleanVar] = {}
        view = self.svc.printers_view()
        if not view:
            ttk.Label(self.plist, text="No printers found in Windows. Add one in Windows Settings, then press Refresh.").pack(anchor="w")
        for p in view:
            var = tk.BooleanVar(value=p["enabled"])
            self.vars[p["name"]] = var
            label = f"{p['name']} — {STATUS_TEXT.get(p['status'], p['status'].lower())}"
            if p["virtual"]:
                label += " (virtual: saves a file, no paper)"
            if not p["installed"]:
                label += " (no longer installed)"
            ttk.Checkbutton(self.plist, text=label, variable=var).pack(anchor="w")
        self.pause_btn.config(text="Resume printing" if self.svc.paused else "Pause printing")

    def _save(self) -> None:
        names = [n for n, v in self.vars.items() if v.get()]
        self.msg.config(text="Saving…", foreground="#444")

        def done(active, exc):
            if self.winfo_exists():
                self.msg.config(text=explain(exc) if exc else "Saved.", foreground="#b00020" if exc else "#1b6e3a")
                self._fill_printers()
        self.ui.run_bg(lambda: self.svc.set_enabled(names), done)

    def _refresh(self) -> None:
        def done(_, exc):
            if self.winfo_exists():
                self._fill_printers()
        self.ui.run_bg(self.svc.refresh_printers, done)

    def _toggle_pause(self) -> None:
        self.svc.set_paused(not self.svc.paused)
        self._fill_printers()

    def _unregister(self) -> None:
        if not messagebox.askyesno("Unregister", "Disconnect this computer from Sesh? Remote printing will stop until an "
                                   "administrator gives you a new enrolment token.", parent=self):
            return

        def done(_, exc):
            if not self.winfo_exists():
                return
            if exc and isinstance(exc, CloudError) and messagebox.askyesno(
                    "Could not reach Sesh", f"{exc.message}\n\nRemove it from this computer anyway? An administrator should "
                    "also revoke it on the Office page.", parent=self):
                self.ui.run_bg(lambda: self.svc.unregister(force_local=True), lambda *_: self.render())
            elif exc and not isinstance(exc, CloudError):
                self.msg.config(text=explain(exc), foreground="#b00020")
            else:
                self.render()
        self.ui.run_bg(self.svc.unregister, done)


class Ui:
    def __init__(self, service: AgentService):
        self.service = service
        self.root = tk.Tk()
        self.root.withdraw()
        self._q: queue.Queue = queue.Queue()
        self.window: MainWindow | None = None
        self.tray = TrayApp(service, self)
        service.add_listener(lambda: self.post(self._on_change))

    def post(self, fn) -> None:
        self._q.put(fn)

    def run_bg(self, fn, done) -> None:
        def work():
            try:
                result, exc = fn(), None
            except Exception as e:  # reported to the user via explain()
                result, exc = None, e
            self.post(lambda: done(result, exc))
        threading.Thread(target=work, daemon=True).start()

    def _pump(self) -> None:
        try:
            while True:
                self._q.get_nowait()()
        except queue.Empty:
            pass
        except Exception:
            log.exception("UI callback failed")
        self.root.after(150, self._pump)

    def _on_change(self) -> None:
        if self.window is not None and self.window.winfo_exists():
            self.window.render()

    def open_window(self) -> None:
        if self.window is not None and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
        else:
            self.window = MainWindow(self)

    def open_logs(self) -> None:
        try:
            os.startfile(str(log_dir()))  # type: ignore[attr-defined]  # Windows only
        except Exception:
            log.warning("could not open the log folder")

    def quit(self) -> None:
        self.tray.stop()
        self.service.stop()
        self.root.quit()

    def run(self) -> None:
        self.tray.start()
        if self.service.state in (State.NOT_REGISTERED, State.REVOKED):
            self.open_window()  # first launch: guide the user straight into setup
        self.root.after(150, self._pump)
        self.root.mainloop()
