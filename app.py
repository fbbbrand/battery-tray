"""Headset + mouse battery, shown on the taskbar next to the notification area.

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

from devices import LOGITECH_VID, CenturionHeadset, HidppMouse, Reading
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


class Device:
    def __init__(self, kind: str, reader):
        self.kind = kind
        self.reader = reader
        self.last: Reading | None = None  # last online reading
        self.stale = False
        self.notified: set[int] = set()

    def apply(self, r: Reading) -> str | None:
        """Store a reading; return a low-battery message if one is due."""
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

    def tooltip(self) -> str:
        if self.last is None:
            return f"{self.reader.name} : non détecté"
        if self.stale:
            return f"{self.last.name} : en veille ({self.last.percent} %)"
        return f"{self.last.name} : {self.last.percent} %" + (" ⚡" if self.last.charging else "")


class App:
    def __init__(self):
        self.devices = [Device("headset", CenturionHeadset()), Device("mouse", HidppMouse())]
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.widget = TaskbarWidget([d.kind for d in self.devices], self.refresh_now, self.quit)
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
        """Re-read right away when a cable is plugged/unplugged: the mouse and the
        headset then show up (or vanish) as extra USB devices."""
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

    def _poll_loop(self) -> None:
        while not self.stop.is_set():
            for d in self.devices:
                try:
                    # HID I/O happens outside the lock so the widget never freezes
                    # while a sleeping mouse times out.
                    r = d.reader.read()
                    with self.lock:
                        prev = (d.last.percent, d.last.charging) if d.last and not d.stale else None
                        msg = d.apply(r)
                    now = (r.percent, r.charging) if r.online else None
                    if now != prev:
                        logging.info("%s: %s -> %s", d.kind, prev, now)
                    if msg:
                        self.tray.notify(msg, "Batterie faible")
                except Exception:  # one bad read must never kill the loop
                    logging.exception("reading %s failed", d.kind)
            with self.lock:
                self.tray.title = "\n".join(d.tooltip() for d in self.devices)[:127]
                self._update_tray_icon()
            self.wake.wait(POLL_SECONDS)
            self.wake.clear()

    def _update_tray_icon(self) -> None:
        known = [d for d in self.devices if d.last]
        if known:
            low = min(known, key=lambda d: d.last.percent)
            self.tray.icon = render(low.kind, low.last.percent, low.last.charging, low.stale)

    def _tick(self) -> None:
        """Runs on the tk thread every 500 ms: push latest state to the widget."""
        with self.lock:
            for d in self.devices:
                if d.last is None:
                    self.widget.set(d.kind, None, False, False)
                else:
                    self.widget.set(d.kind, d.last.percent, d.last.charging, d.stale)

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
