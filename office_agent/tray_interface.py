"""System-tray icon (pystray). Runs detached; every action that needs the UI is posted to the Tk thread."""
from __future__ import annotations

import logging

from PIL import Image, ImageDraw

from .agent_service import AgentService, State

log = logging.getLogger(__name__)
COLORS = {State.CONNECTED: "#2e9e5b", State.OFFLINE: "#e0a100", State.REVOKED: "#c62828", State.NOT_REGISTERED: "#8a8f98"}


def make_icon(color: str) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=color)
    d.rectangle((20, 20, 44, 44), fill="white")  # simple printer-ish glyph
    d.rectangle((24, 14, 40, 22), fill="white", outline=color)
    d.rectangle((24, 38, 40, 50), fill="white", outline=color)
    return img


class TrayApp:
    def __init__(self, service: AgentService, ui):
        import pystray
        self._pystray, self.service, self.ui = pystray, service, ui
        self.icon = pystray.Icon("SeshAgent", make_icon(COLORS[service.state]), "Sesh Agent", pystray.Menu(self._items))
        service.add_listener(self.refresh)

    def _items(self):
        p, s = self._pystray, self.service
        info = lambda text: p.MenuItem(text, lambda: None, enabled=False)
        yield info(s.describe())
        office = s.db.get("office")
        if office:
            yield info(f"Office: {office}")
        yield p.Menu.SEPARATOR
        printers = [x for x in s.printers_view() if x["enabled"]]
        yield p.MenuItem("Printers for Sesh", p.Menu(*(info(f"{x['name']} — {x['status'].lower()}") for x in printers))
                         if printers else p.Menu(info("None selected")))
        yield p.MenuItem("Choose printers / settings…", lambda: self.ui.post(self.ui.open_window))
        yield p.MenuItem("Refresh printers", lambda: (s.refresh_printers(), s.wake(), self.refresh()))
        yield p.MenuItem("Resume printing" if s.paused else "Pause printing", lambda: s.set_paused(not s.paused),
                         enabled=s.state in (State.CONNECTED, State.OFFLINE))
        yield p.MenuItem("View logs", lambda: self.ui.post(self.ui.open_logs))
        yield p.Menu.SEPARATOR
        yield p.MenuItem("Quit Sesh Agent", lambda: self.ui.post(self.ui.quit))

    def refresh(self) -> None:
        try:
            self.icon.icon = make_icon(COLORS[self.service.state])
            self.icon.title = "Sesh Agent: " + self.service.describe()[:100]
            self.icon.update_menu()
        except Exception:
            log.debug("tray refresh failed", exc_info=True)

    def start(self) -> None:
        self.icon.run_detached()

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass
