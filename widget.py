"""Always-visible strip drawn on the taskbar, just left of the notification area.

A borderless, topmost tkinter window whose background is a transparent color
key close to the taskbar color, so it looks native. A 1%-opaque twin window sits
right under it to catch clicks that fall through the transparent pixels.
It hides itself while a fullscreen app (game, video) is in front.
"""
from __future__ import annotations

import ctypes
import logging
import os
import time
import tkinter as tk
from ctypes import wintypes
from typing import Callable

from icons import taskbar_is_light

log = logging.getLogger("battery.widget")

GLYPH_SIZE = 11   # pt, top row (device symbol)
TEXT_SIZE = 9     # pt, bottom row (percentage) - same size as the taskbar clock
BOLT_SIZE = 7     # pt, next to the symbol while charging
GAP = 10          # px between devices
TICK_MS = 250         # z-order re-assert / fullscreen check period
HIDE_AFTER_TICKS = 6  # fullscreen must persist 6 x 250 ms before hiding
RESAMPLE_TICKS = 20   # re-copy the taskbar color every 5 s

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
shell32 = ctypes.windll.shell32

ICON_FONT = "Segoe Fluent Icons"
TEXT_FONT = "Segoe UI Semibold"
# Segoe Fluent Icons: Headphone, Mouse, KeyboardClassic, Game, Devices
GLYPHS = {"headset": "", "mouse": "", "keyboard": "",
          "gamepad": "", "device": ""}
BOLT = ""

GREEN, ORANGE, RED, GREY = "#50dc78", "#ffaa28", "#ff4b4b", "#8c8c8c"

HWND_TOPMOST = wintypes.HWND(-1)  # must be a real 64-bit pointer, not a C int
SWP_FLAGS = 0x0001 | 0x0002 | 0x0010  # NOSIZE | NOMOVE | NOACTIVATE
SWP_MOVE_FLAGS = 0x0004 | 0x0010      # NOZORDER | NOACTIVATE
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x00000080, 0x08000000

# Explicit prototypes: without them ctypes passes/returns 32-bit ints and
# handle arguments like HWND_TOPMOST get silently mangled (ERROR_INVALID_HANDLE).
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = wintypes.HWND
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
WM_NULL = 0x0000
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
gdi32.GetPixel.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.GetPixel.restype = wintypes.DWORD
kernel32 = ctypes.windll.kernel32
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def _rect(hwnd) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _mouse_buttons_down() -> int:
    """Bitmask of mouse buttons currently held: 1 left, 2 right, 4 middle."""
    mask = 0
    for bit, vk in ((1, 0x01), (2, 0x02), (4, 0x04)):
        if user32.GetAsyncKeyState(vk) & 0x8000:
            mask |= bit
    return mask


def _own_popup_menu():
    """Our process's visible popup menu window (class #32768), if any."""
    hwnd = user32.FindWindowExW(None, None, "#32768", None)
    while hwnd:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid() and user32.IsWindowVisible(hwnd):
            return hwnd
        hwnd = user32.FindWindowExW(None, hwnd, "#32768", None)
    return None


def _screen_pixel(x: int, y: int) -> str:
    dc = user32.GetDC(0)
    c = gdi32.GetPixel(dc, x, y)
    user32.ReleaseDC(0, dc)
    if c == 0xFFFFFFFF:  # CLR_INVALID
        return "#1c1c1c"
    r, g, b = c & 0xFF, (c >> 8) & 0xFF, (c >> 16) & 0xFF
    return f"#{r:02x}{g:02x}{b:02x}"


# Windows 11 shell surfaces (Start, Search, tray overflow, Alt+Tab, snipping...)
# are transparent windows covering the whole screen: they are not "fullscreen apps".
SHELL_PROCESSES = {
    "explorer.exe", "shellexperiencehost.exe", "startmenuexperiencehost.exe",
    "searchhost.exe", "searchapp.exe", "textinputhost.exe", "lockapp.exe",
    "screenclippinghost.exe", "snippingtool.exe", "shellhost.exe",
}


def _process_name(hwnd) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        if ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1].lower()
        return ""
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


def fullscreen_app() -> str | None:
    """Return the name of the fullscreen app in front of the primary screen, if any."""
    state = ctypes.c_int()
    if shell32.SHQueryUserNotificationState(ctypes.byref(state)) == 0:
        # 3 = exclusive D3D fullscreen, 4 = presentation mode. State 2 ("busy")
        # is ignored: Windows reports it even with no fullscreen app around.
        if state.value in (3, 4):
            return f"notification state {state.value}"
    fg = user32.GetForegroundWindow()
    if not fg:
        return None
    l, t, r, b = _rect(fg)
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    if not (l <= 0 and t <= 0 and r >= sw and b >= sh):
        return None
    name = _process_name(fg)
    if name in SHELL_PROCESSES:
        return None
    return name or "unknown"


class TaskbarWidget:
    def __init__(self, on_refresh: Callable[[], None], on_quit: Callable[[], None]):
        self.on_refresh = on_refresh
        self.on_quit = on_quit
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        self.fg = "#141414" if taskbar_is_light() else "#f5f5f5"
        self.bg = "#010203"  # color key, replaced from the sampled taskbar color
        self.outer = tk.Frame(self.root, bg=self.bg)
        self.outer.pack(fill="both", expand=True)
        self.frame = tk.Frame(self.outer, bg=self.bg)
        self.frame.pack(expand=True)  # centered vertically in the taskbar height

        # One column per detected device (created on the fly by sync()).
        self.cols: dict[str, dict] = {}
        self._order: list[str] = []
        self._pads: list[tk.Label] = []
        self._bind_clicks(self.outer)
        self._bind_clicks(self.frame)

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Rafraîchir", command=self.on_refresh)
        self.menu.add_separator()
        self.menu.add_command(label="Quitter", command=self.on_quit)

        self.hwnds: list[int] = []
        self.catcher = self._make_click_catcher()
        self._geometry = None
        self._hidden = True
        self._fullscreen_ticks = 0

    # ------------------------------------------------------------------ public
    def sync(self, items: list[tuple[str, str, int | None, bool, bool]]) -> None:
        """items: (key, kind, percent, charging, stale) in display order."""
        keys = [k for k, *_ in items]
        for key in list(self.cols):
            if key not in keys:  # device gone (unplugged receiver/dongle)
                col = self.cols.pop(key)
                self._pads.remove(col["pad"])
                col["frame"].destroy()
        for key, kind, *_ in items:
            if key not in self.cols:
                self.cols[key] = self._make_column(kind)
        if keys != self._order:
            for c in self.cols.values():
                c["frame"].pack_forget()
            for i, key in enumerate(keys):
                self.cols[key]["frame"].pack(side="left", padx=(GAP if i else 0, 0))
            self._order = keys
        for key, kind, percent, charging, stale in items:
            self._set(key, percent, charging, stale)

    def _make_column(self, kind: str) -> dict:
        """Symbol (+ charging bolt) on top, percentage below."""
        col = tk.Frame(self.frame, bg=self.bg)
        top = tk.Frame(col, bg=self.bg)
        top.pack()
        # invisible left twin of the bolt keeps the symbol centered
        pad = tk.Label(top, text=BOLT, font=(ICON_FONT, BOLT_SIZE), bd=0, padx=0,
                       bg=self.bg, fg=self.bg)
        glyph = tk.Label(top, text=GLYPHS.get(kind, GLYPHS["device"]),
                         font=(ICON_FONT, GLYPH_SIZE), fg=self.fg, bg=self.bg, bd=0, padx=1)
        bolt = tk.Label(top, text=BOLT, font=(ICON_FONT, BOLT_SIZE), bd=0, padx=0,
                        bg=self.bg, fg=self.bg)
        for w in (pad, glyph, bolt):
            w.pack(side="left")
        pct = tk.Label(col, text="–", font=(TEXT_FONT, TEXT_SIZE), fg=GREY, bg=self.bg,
                       bd=0, pady=0)
        pct.pack()
        self._pads.append(pad)
        for w in (col, top, pad, glyph, bolt, pct):
            self._bind_clicks(w)
        return {"frame": col, "glyph": glyph, "pct": pct, "bolt": bolt, "pad": pad}

    def _bind_clicks(self, w) -> None:
        w.bind("<Button-1>", lambda e: self.on_refresh())
        w.bind("<Button-3>", self._popup)

    def _set(self, key: str, percent: int | None, charging: bool, stale: bool) -> None:
        c = self.cols[key]
        glyph, pct, bolt = c["glyph"], c["pct"], c["bolt"]
        if percent is None:
            pct.config(text="–", fg=GREY)
        else:
            if stale:
                color = GREY
            elif charging:
                color = GREEN
            elif percent <= 15:
                color = RED
            elif percent <= 30:
                color = ORANGE
            else:
                color = self.fg
            pct.config(text=f"{percent}%", fg=color)
        glyph.config(fg=GREY if percent is None or stale else self.fg)
        # The bolt keeps its width when hidden (drawn in the bg color) so the
        # layout doesn't jump when charging starts or stops.
        bolt.config(fg=GREEN if charging and not stale and percent is not None else self.bg)

    def run(self, tick: Callable[[], None]) -> None:
        def loop():
            tick()
            self._place()
            self.root.after(TICK_MS, loop)

        self.root.after(0, loop)
        self.root.mainloop()

    def close(self) -> None:
        self.root.after(0, self.root.destroy)

    # ----------------------------------------------------------------- private
    def _popup(self, event=None) -> None:
        """Open the menu at a fixed spot: just above the widget, left-aligned,
        wherever the click landed (like the menus of tray icons)."""
        # A popup menu only closes on an outside click if its window is in the
        # foreground (our windows never activate on their own: WS_EX_NOACTIVATE).
        # Microsoft's recipe: SetForegroundWindow before, WM_NULL after (KB135788).
        hwnd = self.hwnds[-1] if self.hwnds else None
        if hwnd:
            user32.SetForegroundWindow(hwnd)
        # Belt and braces (foreground can be refused): watch for a new click
        # outside the menu while it's open and close it ourselves.
        self._menu_buttons = _mouse_buttons_down()
        self._menu_seen = False
        self._menu_watch_started = time.monotonic()
        self.root.after(30, self._watch_menu)
        # Posting at the taskbar's top edge: the menu can't fit in the taskbar
        # height below, so Windows flips it upward and its bottom edge lands
        # exactly on the taskbar. (tk's reqheight for native menus is wrong.)
        try:
            self.menu.tk_popup(self.root.winfo_rootx(), self.root.winfo_rooty())
        finally:
            self.menu.grab_release()
            if hwnd:
                user32.PostMessageW(hwnd, WM_NULL, 0, 0)

    def _watch_menu(self) -> None:
        """Runs every 30 ms while our popup menu is open (Tk timers keep firing
        during the menu's modal loop). Closes it on a new outside click."""
        menu = _own_popup_menu()
        if menu is None:
            # not shown yet (give it 1 s) or already closed: stop watching
            if self._menu_seen or time.monotonic() - self._menu_watch_started > 1.0:
                return
            self.root.after(30, self._watch_menu)
            return
        self._menu_seen = True
        down = _mouse_buttons_down()
        new_press = down & ~self._menu_buttons  # ignore the button that opened it
        self._menu_buttons = down
        if new_press:
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            l, t, r, b = _rect(menu)
            if not (l <= pt.x < r and t <= pt.y < b):
                user32.EndMenu()
                return
        self.root.after(30, self._watch_menu)

    def _all_widgets(self, w=None):
        w = w or self.outer
        yield w
        for c in w.winfo_children():
            if not isinstance(c, tk.Menu):
                yield from self._all_widgets(c)

    def _apply_key(self, color: str) -> None:
        # The background is a transparent color key. The key is the taskbar color
        # nudged by 1 so anti-aliased text edges blend into (almost) the real
        # taskbar color instead of leaving a visible halo.
        r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        key = f"#{r:02x}{g:02x}{(b + 1) % 256:02x}"
        if key == self.bg:
            return
        old, self.bg = self.bg, key
        for w in self._all_widgets():
            w.config(bg=key)
            if isinstance(w, tk.Label) and w.cget("fg") == old:
                w.config(fg=key)  # hidden bolts follow the key
        for p in self._pads:
            p.config(fg=key)
        self.root.attributes("-transparentcolor", key)

    def _sample_taskbar(self, taskbar, x: int, y: int) -> None:
        """Copy the taskbar color from a bare spot left of the widget."""
        root = user32.GetAncestor(user32.WindowFromPoint(wintypes.POINT(x, y)), 2)
        if root == taskbar:  # skip if a popup/menu currently covers that spot
            self._apply_key(_screen_pixel(x, y))

    def _make_click_catcher(self) -> tk.Toplevel:
        """Window right under the widget, 1% opaque: it receives the clicks that
        pass through the widget's transparent (color-keyed) pixels, so the whole
        rectangle is clickable instead of only the glyphs."""
        c = tk.Toplevel(self.root)
        c.withdraw()
        c.overrideredirect(True)
        c.configure(bg="black")
        c.attributes("-topmost", True)
        c.attributes("-alpha", 0.01)
        c.bind("<Button-1>", lambda e: self.on_refresh())
        c.bind("<Button-3>", self._popup)
        return c

    def _place(self) -> None:
        app = fullscreen_app()
        self._fullscreen_ticks = self._fullscreen_ticks + 1 if app else 0
        reason = None
        if self._fullscreen_ticks >= HIDE_AFTER_TICKS:
            reason = f"fullscreen app in front ({app})"
        elif not self.cols:
            reason = "no battery device detected"
        if reason:
            if not self._hidden:
                log.info("hidden: %s", reason)
                self.root.withdraw()
                self.catcher.withdraw()
                self._hidden = True
            return

        taskbar = user32.FindWindowW("Shell_TrayWnd", None)
        if not taskbar:
            return  # explorer not ready (startup) or restarting
        tl, tt, tr, tb = _rect(taskbar)
        tray = user32.FindWindowExW(taskbar, None, "TrayNotifyWnd", None)
        right = _rect(tray)[0] - 6 if tray else tr - 300

        self.root.update_idletasks()
        w, h = self.frame.winfo_reqwidth(), tb - tt
        x = right - w
        geometry = f"{w}x{h}+{x}+{tt}"

        self._ticks = getattr(self, "_ticks", 0) + 1
        moved = self._hidden or geometry != self._geometry
        if moved or self._ticks % RESAMPLE_TICKS == 0:  # follow theme changes
            self._sample_taskbar(taskbar, max(tl, x - 4), tt + h // 2)
        if moved:
            self.root.geometry(geometry)
            self.catcher.geometry(geometry)
            # Tk resizes borderless windows but sometimes ignores the move part
            # of geometry(): position them through Win32 as well.
            for hwnd in self.hwnds:
                user32.SetWindowPos(hwnd, None, x, tt, w, h, SWP_MOVE_FLAGS)
            self._geometry = geometry
        if self._hidden:
            log.info("shown at %s", geometry)
            self.catcher.deiconify()
            self.root.deiconify()
            self._hidden = False
            self.hwnds = []
            for win in (self.catcher, self.root):  # bottom first
                hwnd = int(win.wm_frame(), 16)
                ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
                self.hwnds.append(hwnd)
        # The taskbar is topmost too and jumps back in front whenever it's
        # clicked: re-assert our z-order every tick, catcher first so the
        # visible widget ends up right above it.
        for hwnd in self.hwnds:
            if not user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_FLAGS):
                if not getattr(self, "_z_error_logged", False):
                    log.warning("SetWindowPos failed: %s", ctypes.GetLastError())
                    self._z_error_logged = True
