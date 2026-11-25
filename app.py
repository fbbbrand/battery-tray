"""Battery of every Logitech wireless device found (headsets, mice, keyboards...),
shown on the taskbar next to the notification area.

Run without a console window:  .venv\\Scripts\\pythonw.exe app.py
"""
from __future__ import annotations

import ctypes
import logging
import os
import socket
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import hid
import pystray

from devices import LOGITECH_VID, Reading, Scanner
from icons import render
from widget import TaskbarWidget

POLL_SECONDS = 60          # regular battery re-read
USB_SCAN_SECONDS = 2       # cable plug/unplug detection (a scan costs ~13 ms)
REREAD_AFTER_PLUG = (1.5, 5.0)  # seconds after a USB change: re-read twice
LOW_THRESHOLDS = (20, 10)  # notify once when crossing each of these
CONTROL_PORT = 47821       # localhost only: a new instance tells the old one to quit


def take_over_single_instance() -> socket.socket:
    """Ask a running instance to quit, then own the control port."""
    try:
        with socket.create_connection(("127.0.0.1", CONTROL_PORT), timeout=1) as s:
            s.sendall(b"quit")
        logging.info("asked previous instance to quit")
    except OSError:
        pass  # nobody running
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    for _ in range(50):  # the old instance needs a moment to release the port
        try:
            srv.bind(("127.0.0.1", CONTROL_PORT))
            break
        except OSError:
            time.sleep(0.1)
    else:
        raise SystemExit("another instance refuses to quit")
    srv.listen(1)
    return srv


class DeviceState:
    """What we know about one physical device across polls."""

    def __init__(self, r: Reading):
        self.key, self.name, self.kind = r.key, r.name, r.kind
        self.last: Reading | None = None  # last online reading
        self.stale = False
        self.notified: set[int] = set()

    def apply(self, r: Reading) -> str | None:
        """Store a reading; return a low-battery message if one is due."""
        self.name, self.kind = r.name, r.kind
        if not r.online:
            self.stale = self.last is not None
            return None
        self.last, self.stale = r, False
        if r.charging or r.percent > max(LOW_THRESHOLDS) + 5:
            self.notified.clear()
            return None
        for t in LOW_THRESHOLDS:
            if r.percent <= t and t not in self.notified:
                self.notified.update(x for x in LOW_THRESHOLDS if x >= t)
                return f"{r.name} : batterie à {r.percent} %"
        return None

    def snapshot(self) -> tuple[int, bool] | None:
        return (self.last.percent, self.last.charging) if self.last and not self.stale else None

    def tooltip(self) -> str:
        if self.last is None:
            return f"{self.name} : en veille"
        if self.stale:
            return f"{self.name} : en veille ({self.last.percent} %)"
        return f"{self.name} : {self.last.percent} %" + (" ⚡" if self.last.charging else "")


class App:
    def __init__(self):
        self.scanner = Scanner()
        self.states: dict[str, DeviceState] = {}
        self.order: list[str] = []
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.widget = TaskbarWidget(self.refresh_now, self.quit)
        # Hidden-by-default tray icon: carries notifications and a fallback menu.
        self.tray = pystray.Icon(
            "battery-tray", icon=render("headset", None), title="Batterie",
            menu=pystray.Menu(
                pystray.MenuItem("Rafraîchir", lambda: self.refresh_now(), default=True),
                pystray.MenuItem("Quitter", lambda: self.quit()),
            ),
        )

    def refresh_now(self) -> None:
        self.wake.set()

    def quit(self) -> None:
        self.stop.set()
        self.wake.set()
        self.tray.stop()
        self.widget.close()

    def _usb_watch_loop(self) -> None:
        """Re-read right away when something Logitech is plugged/unplugged
        (a cable, a receiver, a dongle)."""
        def scan():
            return frozenset(d["product_id"] for d in hid.enumerate(LOGITECH_VID))

        prev = scan()
        while not self.stop.wait(USB_SCAN_SECONDS):
            cur = scan()
            if cur == prev:
                continue
            logging.info("USB change: +%s -%s", sorted(hex(p) for p in cur - prev),
                         sorted(hex(p) for p in prev - cur))
            prev = cur
            waited = 0.0
            for t in REREAD_AFTER_PLUG:  # let the device settle, then read twice
                if self.stop.wait(t - waited):
                    return
                waited = t
                self.wake.set()

    def _poll_once(self) -> None:
        # HID I/O happens outside the lock so the widget never freezes while a
        # sleeping device times out.
        readings = self.scanner.read_all()
        messages = []
        with self.lock:
            keys = [r.key for r in readings]
            for key in list(self.states):
                if key not in keys:  # its receiver/dongle was unplugged
                    logging.info("%s: removed", key)
                    del self.states[key]
            for r in readings:
                st = self.states.get(r.key)
                if st is None:
                    st = self.states[r.key] = DeviceState(r)
                    logging.info("%s: new %s", r.key, r.kind)
                before = st.snapshot()
                msg = st.apply(r)
                if st.snapshot() != before:
                    logging.info("%s: %s -> %s", r.key, before, st.snapshot())
                if msg:
                    messages.append(msg)
            self.order = keys
            self.tray.title = ("\n".join(self.states[k].tooltip() for k in keys)
                               or "Aucun appareil Logitech détecté")[:127]
            self._update_tray_icon()
        for msg in messages:
            self.tray.notify(msg, "Batterie faible")

    def _poll_loop(self) -> None:
        while not self.stop.is_set():
            try:
                self._poll_once()
            except Exception:  # one bad poll must never kill the loop
                logging.exception("poll failed")
            self.wake.wait(POLL_SECONDS)
            self.wake.clear()

    def _update_tray_icon(self) -> None:
        known = [s for s in self.states.values() if s.last]
        if known:
            low = min(known, key=lambda s: s.last.percent)
            self.tray.icon = render(low.kind, low.last.percent, low.last.charging, low.stale)

    def _tick(self) -> None:
        """Runs on the tk thread every 250 ms: push the latest state to the widget."""
        with self.lock:
            items = []
            for key in self.order:
                s = self.states[key]
                if s.last is None:
                    items.append((key, s.kind, None, False, False))
                else:
                    items.append((key, s.kind, s.last.percent, s.last.charging, s.stale))
        self.widget.sync(items)

    def _control_loop(self, srv: socket.socket) -> None:
        while not self.stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                if conn.recv(16) == b"quit":
                    logging.info("replaced by a new instance, quitting")
                    srv.close()
                    self.quit()
                    return

    def run(self, srv: socket.socket) -> None:
        threading.Thread(target=self._control_loop, args=(srv,), daemon=True).start()
        threading.Thread(target=self.tray.run, daemon=True).start()
        threading.Thread(target=self._poll_loop, daemon=True).start()
        threading.Thread(target=self._usb_watch_loop, daemon=True).start()
        self.widget.run(self._tick)


def log_path() -> Path:
    # A PyInstaller one-file exe runs from a temp dir wiped on exit: log next to
    # the installed exe's data in %LOCALAPPDATA% instead.
    if getattr(sys, "frozen", False):
        d = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "BatteryTray"
        d.mkdir(parents=True, exist_ok=True)
        return d / "battery_tray.log"
    return Path(__file__).with_name("battery_tray.log")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[RotatingFileHandler(log_path(), maxBytes=200_000, backupCount=1,
                                      encoding="utf-8")],
    )
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixel coordinates
    App().run(take_over_single_instance())
