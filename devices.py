"""Battery readers for Logitech LIGHTSPEED devices.

- Mouse: HID++ 2.0 through the LIGHTSPEED receiver (feature 0x1004 Unified Battery).
- Headset PRO X 2: Logitech "Centurion" protocol (64-byte frames, report 0x51,
  usage page 0xFFA0), battery sub-feature 0x0104 reached through the bridge 0x0003.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import hid

LOGITECH_VID = 0x046D


@dataclass
class Reading:
    name: str
    percent: int | None = None  # None = device off / unreachable
    charging: bool = False

    @property
    def online(self) -> bool:
        return self.percent is not None


def _find_path(pid: int, usage_page: int, usage: int | None = None) -> bytes | None:
    for d in hid.enumerate(LOGITECH_VID, pid):
        if d["usage_page"] == usage_page and (usage is None or d["usage"] == usage):
            return d["path"]
    return None


def _drain(dev: hid.device) -> None:
    """Discard pending input reports so stale replies don't get matched.

    Note: hidapi's read(n, 0) blocks forever, so use a 1 ms timeout.
    """
    for _ in range(64):
        if not dev.read(64, 1):
            break


# --------------------------------------------------------------------------- mouse
class HidppMouse:
    """HID++ 2.0 device behind a LIGHTSPEED receiver."""

    SW_ID = 0x0A
    FEAT_NAME = 0x0005
    FEAT_UNIFIED_BATTERY = 0x1004
    FEAT_BATTERY_STATUS = 0x1000

    # (product id, HID++ device index). When charging over USB the mouse shows up
    # as its own wired device (index 0xFF); otherwise it sits behind the receiver.
    DEFAULT_ROUTES = ((0xC0A9, 0xFF), (0xC54F, 0x01))

    def __init__(self, routes=DEFAULT_ROUTES, fallback_name: str = "Souris"):
        self.routes = routes
        self.idx = routes[0][1]
        self.name = fallback_name
        self._features: dict[int, int] = {}
        self._route = None

    def _request(self, dev, feat_index: int, fn: int, params: bytes = b"",
                 timeout: float = 0.5) -> bytes | None:
        msg = bytes([0x11, self.idx, feat_index, (fn << 4) | self.SW_ID]) + params
        dev.write(msg.ljust(20, b"\x00"))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = dev.read(20, 100)
            if not r or r[1] != self.idx:
                continue
            r = bytes(r)
            if r[0] in (0x10, 0x11) and r[2] == 0x8F:
                return None  # HID++ 1.0 error: device asleep / not paired
            if r[2] == 0xFF and r[3] == feat_index:
                return None  # HID++ 2.0 error
            if r[2] == feat_index and (r[3] & 0x0F) == self.SW_ID:
                return r
        return None

    def _feature_index(self, dev, feature_id: int) -> int | None:
        if feature_id not in self._features:
            r = self._request(dev, 0x00, 0, feature_id.to_bytes(2, "big"))
            if r is None:
                return None
            self._features[feature_id] = r[4]  # 0 = not supported
        return self._features[feature_id] or None

    def _read_name(self, dev) -> None:
        fi = self._feature_index(dev, self.FEAT_NAME)
        if fi is None:
            return
        r = self._request(dev, fi, 0)
        if r is None:
            return
        length, name = r[4], b""
        while len(name) < length:
            r = self._request(dev, fi, 1, bytes([len(name)]))
            if r is None:
                return
            name += r[4:20]
        self.name = name[:length].decode(errors="replace").strip() or self.name

    def read(self) -> Reading:
        for pid, idx in self.routes:
            path = _find_path(pid, 0xFF43, 0x0302) or _find_path(pid, 0xFF00, 0x0002)
            if path is None:
                continue
            if self._route != (pid, idx):  # transport changed: rediscover features
                self._route, self.idx = (pid, idx), idx
                self._features.clear()
            r = self._read_path(path)
            if r.online:
                return r
        return Reading(self.name)

    def _read_path(self, path: bytes) -> Reading:
        dev = hid.device()
        try:
            dev.open_path(path)
            _drain(dev)
            if not self._features:
                self._read_name(dev)

            fi = self._feature_index(dev, self.FEAT_UNIFIED_BATTERY)
            if fi is not None:
                r = self._request(dev, fi, 1)  # get_status
                if r is not None:
                    # r[4]=state of charge %, r[6]: 1/2 charging, 3 full
                    return Reading(self.name, r[4], r[6] in (1, 2))

            fi = self._feature_index(dev, self.FEAT_BATTERY_STATUS)
            if fi is not None:
                r = self._request(dev, fi, 0)
                if r is not None:
                    return Reading(self.name, r[4], r[6] in (1, 2))

            # Nothing answered: forget cached indexes, retry discovery next time.
            self._features.clear()
            return Reading(self.name)
        except OSError:
            self._features.clear()
            return Reading(self.name)
        finally:
            dev.close()


# ------------------------------------------------------------------------- headset
class CenturionHeadset:
    """Logitech PRO X 2 LIGHTSPEED (and similar) using the Centurion protocol."""

    REPORT_ID = 0x51
    SW_ID = 0x0A
    FEAT_BRIDGE = 0x0003
    SUBFEAT_BATTERY = 0x0104
    # Values observed on PRO X 2 firmware, used if discovery fails.
    DEFAULT_BRIDGE, DEFAULT_BATTERY = 0x03, 0x04

    def __init__(self, pid: int = 0x0AF7, name: str = "PRO X 2 LIGHTSPEED"):
        # The wired PID (0x0AF8, cable only) doesn't answer this protocol: dongle only.
        self.pid = pid
        self.name = name
        self.bridge: int | None = None
        self.battery: int | None = None

    def _write(self, dev, payload: bytes) -> None:
        frame = bytes([self.REPORT_ID, len(payload) + 1, 0x00]) + payload
        dev.write(frame.ljust(64, b"\x00"))

    def _read_until(self, dev, match, timeout: float) -> bytes | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = dev.read(64, 100)
            if r and r[0] == self.REPORT_ID:
                r = bytes(r)
                if match(r):
                    return r
        return None

    def _discover(self, dev) -> None:
        # Root (index 0) getFeature(0x0003) -> bridge index in byte 5.
        self._write(dev, bytes([0x00, 0x00 | self.SW_ID]) + self.FEAT_BRIDGE.to_bytes(2, "big"))
        r = self._read_until(dev, lambda r: r[3] == 0x00 and (r[4] & 0x0F) == self.SW_ID, 0.6)
        bridge = r[5] if r and r[5] else self.DEFAULT_BRIDGE

        # Through the bridge: sub-device root getFeature(0x0104) -> battery index.
        sub = bytes([0x00, 0x00, 0x00 | self.SW_ID]) + self.SUBFEAT_BATTERY.to_bytes(2, "big")
        self._write(dev, bytes([bridge, 0x10 | self.SW_ID, 0x00, len(sub)]) + sub)
        r = self._read_until(
            dev, lambda r: r[3] == bridge and (r[4] & 0x0F) == 0 and r[8] == 0x00, 0.6)
        battery = r[10] if r and r[10] else self.DEFAULT_BATTERY

        self.bridge, self.battery = bridge, battery

    def read(self) -> Reading:
        path = _find_path(self.pid, 0xFFA0)
        if path is None:
            return Reading(self.name)  # dongle unplugged
        dev = hid.device()
        try:
            dev.open_path(path)
            _drain(dev)
            if self.bridge is None:
                self._discover(dev)
            sub = bytes([0x00, self.battery, 0x00 | self.SW_ID])
            self._write(dev, bytes([self.bridge, 0x10 | self.SW_ID, 0x00, len(sub)]) + sub)
            # Reply: [51 len 00 bridge 10 ...  sub-index fn|sw  pct ?? charge]
            r = self._read_until(
                dev,
                lambda r: r[3] == self.bridge and (r[4] & 0x0F) == 0 and r[8] == self.battery,
                1.0,
            )
            if r is None or r[10] > 100:
                return Reading(self.name)  # headset off (dongle only)
            return Reading(self.name, r[10], r[12] in (1, 2))
        except OSError:
            return Reading(self.name)
        finally:
            dev.close()


if __name__ == "__main__":
    for d in (CenturionHeadset(), HidppMouse()):
        print(d.read())
