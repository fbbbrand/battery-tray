"""Battery readers for Logitech wireless devices, discovered automatically.

Two protocols:

- HID++ 2.0 (mice, keyboards, many headsets), on the vendor "long report"
  interface (usage page 0xFF00 or 0xFF43). A device can be plugged directly
  (cable, or a dongle that *is* the device: index 0xFF) or sit behind a receiver
  (Unifying / LIGHTSPEED / Bolt: indexes 1..6). Battery features tried in order:
  0x1004 Unified Battery, 0x1000 Battery Status, 0x1001 Battery Voltage,
  0x1F20 ADC Measurement (voltage, used by several headsets).
- Logitech "Centurion" (PRO X 2 LIGHTSPEED headset and similar): 64-byte frames
  with report id 0x51 on usage page 0xFFA0; battery sub-feature 0x0104 reached
  through the bridge feature 0x0003.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import hid

log = logging.getLogger("battery.devices")

LOGITECH_VID = 0x046D

# HID++ 0x0005 getDeviceType -> widget glyph kind
DEVICE_TYPES = {0: "keyboard", 2: "keyboard", 3: "mouse", 4: "mouse", 5: "mouse",
                8: "headset", 12: "gamepad", 15: "headset"}
# Order of columns in the widget
KIND_ORDER = {"headset": 0, "mouse": 1, "keyboard": 2, "gamepad": 3, "device": 4}

# Li-ion voltage (mV) -> remaining %, same curve as Solaar uses.
VOLTAGE_CURVE = ((4186, 100), (4067, 90), (3989, 80), (3922, 70), (3859, 60),
                 (3811, 50), (3778, 40), (3751, 30), (3717, 20), (3671, 10),
                 (3646, 5), (3579, 2), (3500, 0))


def voltage_to_percent(mv: int) -> int:
    for v, pct in VOLTAGE_CURVE:
        if mv >= v:
            return pct
    return 0


@dataclass
class Reading:
    key: str            # identifies the physical device (deduplication)
    name: str
    kind: str           # headset / mouse / keyboard / gamepad / device
    percent: int | None = None  # None = device asleep / off / unreachable
    charging: bool = False
    direct: bool = False        # read over a cable/own dongle (preferred source)

    @property
    def online(self) -> bool:
        return self.percent is not None


def _drain(dev: hid.device) -> None:
    """Discard pending input reports so stale replies don't get matched.
    (hidapi's read(n, 0) blocks forever, so use a 1 ms timeout.)"""
    for _ in range(64):
        if not dev.read(64, 1):
            break


def _open(path: bytes) -> hid.device:
    dev = hid.device()
    dev.open_path(path)
    _drain(dev)
    return dev


# =============================================================================== HID++
class HidppDevice:
    """One HID++ 2.0 device: a HID interface path + device index."""

    SW_ID = 0x0A
    FEAT_NAME = 0x0005
    BATTERY_FEATURES = (0x1004, 0x1000, 0x1001, 0x1F20)

    def __init__(self, path: bytes, index: int, direct: bool, fallback_name: str):
        self.path = path
        self.idx = index
        self.direct = direct
        self.name = fallback_name
        self.kind = "device"
        self.features: dict[int, int] = {}
        self.battery_feature: int | None = None
        self.known = False  # answered at least once with a battery feature

    # -- transport --------------------------------------------------------------------
    def request(self, dev, feat_index: int, fn: int, params: bytes = b"",
                timeout: float = 0.5) -> bytes | None:
        msg = bytes([0x11, self.idx, feat_index, (fn << 4) | self.SW_ID]) + params
        dev.write(msg.ljust(20, b"\x00"))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = dev.read(20, 50)
            if not r or r[1] != self.idx:
                continue
            r = bytes(r)
            if r[2] == 0x8F:
                return None  # HID++ 1.0 error: device asleep / not paired
            if r[2] == 0xFF and r[3] == feat_index:
                return None  # HID++ 2.0 error
            if r[2] == feat_index and (r[3] & 0x0F) == self.SW_ID:
                return r
        return None

    def ping(self, dev, timeout: float = 0.3) -> bool:
        return self.request(dev, 0x00, 1, b"\x00\x00\xAA", timeout) is not None

    def feature_index(self, dev, feature_id: int) -> int | None:
        if feature_id not in self.features:
            r = self.request(dev, 0x00, 0, feature_id.to_bytes(2, "big"))
            if r is None:
                return None  # unreachable: don't cache
            self.features[feature_id] = r[4]  # 0 = not supported
        return self.features[feature_id] or None

    # -- discovery --------------------------------------------------------------------
    def identify(self, dev) -> bool:
        """Learn name, type and battery feature. True if it has a battery."""
        fi = self.feature_index(dev, self.FEAT_NAME)
        if fi is not None:
            r = self.request(dev, fi, 0)
            if r is not None:
                length, name = r[4], b""
                while len(name) < length:
                    part = self.request(dev, fi, 1, bytes([len(name)]))
                    if part is None:
                        break
                    name += part[4:20]
                else:
                    self.name = name[:length].decode(errors="replace").strip() or self.name
            r = self.request(dev, fi, 2)
            if r is not None:
                self.kind = DEVICE_TYPES.get(r[4], "device")
        for feat in self.BATTERY_FEATURES:
            if self.feature_index(dev, feat):
                self.battery_feature = feat
                return True
        return False

    # -- battery ----------------------------------------------------------------------
    def _read_battery(self, dev) -> tuple[int, bool] | None:
        feat = self.battery_feature
        fi = self.features.get(feat)
        if feat == 0x1004:  # Unified Battery: get_status
            r = self.request(dev, fi, 1)
            if r is None:
                return None
            soc, level, status = r[4], r[5], r[6]
            pct = soc or {8: 100, 4: 50, 2: 20, 1: 5}.get(level, 0)
            return pct, status in (1, 2, 3)  # charging, slow charging, full on cable
        if feat == 0x1000:  # Battery Status
            r = self.request(dev, fi, 0)
            if r is None:
                return None
            return r[4], r[6] in (1, 2, 3, 4)
        if feat == 0x1001:  # Battery Voltage
            r = self.request(dev, fi, 0)
            if r is None:
                return None
            mv, flags = int.from_bytes(r[4:6], "big"), r[6]
            return voltage_to_percent(mv), bool(flags & 0x80)
        if feat == 0x1F20:  # ADC Measurement (headsets)
            r = self.request(dev, fi, 0)
            if r is None:
                return None
            mv, flags = int.from_bytes(r[4:6], "big"), r[6]
            if not flags & 0x01:
                return None  # headset not connected to its dongle
            return voltage_to_percent(mv), bool(flags & 0x02)
        return None

    def read(self) -> Reading | None:
        """None if this is not (or not yet known to be) a battery device."""
        try:
            dev = _open(self.path)
        except OSError:
            return self._offline()
        try:
            if self.battery_feature is None:
                if not self.identify(dev):
                    return None
                self.known = True
                log.info("found %s %r (HID++ idx %#x, feature %#06x)",
                         self.kind, self.name, self.idx, self.battery_feature)
            res = self._read_battery(dev)
            if res is None:
                return self._offline()
            pct, charging = res
            return Reading(self.name, self.name, self.kind, min(pct, 100), charging, self.direct)
        except OSError:
            return self._offline()
        finally:
            dev.close()

    def _offline(self) -> Reading | None:
        return Reading(self.name, self.name, self.kind, direct=self.direct) if self.known else None


class HidppInterface:
    """A HID++ long-report interface: either a device itself, or a receiver."""

    MAX_SLOTS = 6

    def __init__(self, path: bytes, product: str):
        self.path = path
        self.product = product
        self.role: str | None = None  # "device" / "receiver" / None (unknown yet)
        self.devices: dict[int, HidppDevice] = {}

    def _classify(self) -> None:
        probe = HidppDevice(self.path, 0xFF, True, self.product)
        dev = _open(self.path)
        try:
            if probe.ping(dev) and probe.identify(dev) and probe.kind != "receiver":
                self.role = "device"
                probe.known = True
                self.devices[0xFF] = probe
                log.info("found %s %r (HID++ direct, feature %#06x)",
                         probe.kind, probe.name, probe.battery_feature)
            else:
                self.role = "receiver"
        finally:
            dev.close()

    def _probe_slots(self) -> None:
        """Look for paired devices that answer now (others may be asleep)."""
        dev = _open(self.path)
        try:
            for slot in range(1, self.MAX_SLOTS + 1):
                if slot not in self.devices:
                    d = HidppDevice(self.path, slot, False, self.product)
                    if d.ping(dev, timeout=0.25):
                        self.devices[slot] = d
        finally:
            dev.close()

    def readings(self) -> list[Reading]:
        try:
            if self.role is None:
                self._classify()
            if self.role == "receiver":
                self._probe_slots()
        except OSError:
            pass
        out = []
        for d in list(self.devices.values()):
            r = d.read()
            if r is not None:
                out.append(r)
        return out


# ============================================================================ Centurion
class CenturionHeadset:
    """Logitech headset speaking the Centurion protocol (e.g. PRO X 2 LIGHTSPEED)."""

    REPORT_ID = 0x51
    SW_ID = 0x0A
    FEAT_BRIDGE = 0x0003
    SUBFEAT_BATTERY = 0x0104

    def __init__(self, path: bytes, product: str):
        self.path = path
        self.name = product or "Casque Logitech"
        self.bridge: int | None = None
        self.battery: int | None = None
        self.known = False

    def _write(self, dev, payload: bytes) -> None:
        frame = bytes([self.REPORT_ID, len(payload) + 1, 0x00]) + payload
        dev.write(frame.ljust(64, b"\x00"))

    def _read_until(self, dev, match, timeout: float) -> bytes | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = dev.read(64, 50)
            if r and r[0] == self.REPORT_ID:
                r = bytes(r)
                if len(r) > 12 and match(r):
                    return r
        return None

    def _discover(self, dev) -> bool:
        # Root (index 0) getFeature(0x0003) -> bridge index in byte 5.
        self._write(dev, bytes([0x00, self.SW_ID]) + self.FEAT_BRIDGE.to_bytes(2, "big"))
        r = self._read_until(dev, lambda r: r[3] == 0x00 and (r[4] & 0x0F) == self.SW_ID, 0.5)
        if not r or not r[5]:
            return False  # not a Centurion device
        bridge = r[5]
        # Through the bridge: sub-device root getFeature(0x0104) -> battery index.
        sub = bytes([0x00, 0x00, self.SW_ID]) + self.SUBFEAT_BATTERY.to_bytes(2, "big")
        self._write(dev, bytes([bridge, 0x10 | self.SW_ID, 0x00, len(sub)]) + sub)
        r = self._read_until(
            dev, lambda r: r[3] == bridge and (r[4] & 0x0F) == 0 and r[8] == 0x00, 0.6)
        if not r or not r[10]:
            return False  # headset off, or no battery sub-feature: retry later
        self.bridge, self.battery = bridge, r[10]
        return True

    def readings(self) -> list[Reading]:
        try:
            dev = _open(self.path)
        except OSError:
            return self._offline()
        try:
            if self.bridge is None and not self._discover(dev):
                return self._offline()
            sub = bytes([0x00, self.battery, self.SW_ID])
            self._write(dev, bytes([self.bridge, 0x10 | self.SW_ID, 0x00, len(sub)]) + sub)
            # Reply: [51 len 00 bridge 10 .. .. .. sub-index fn|sw pct pct charge]
            r = self._read_until(
                dev, lambda r: r[3] == self.bridge and (r[4] & 0x0F) == 0 and r[8] == self.battery,
                1.0)
            if r is None or r[10] > 100:
                return self._offline()  # headset off (dongle only)
            if not self.known:
                self.known = True
                log.info("found headset %r (Centurion)", self.name)
            return [Reading(self.name, self.name, "headset", r[10], r[12] in (1, 2), True)]
        except OSError:
            return self._offline()
        finally:
            dev.close()

    def _offline(self) -> list[Reading]:
        return [Reading(self.name, self.name, "headset", direct=True)] if self.known else []


# ============================================================================== scanner
class Scanner:
    """Finds every Logitech HID++ / Centurion interface and reads all batteries."""

    def __init__(self):
        self.interfaces: dict[bytes, HidppInterface | CenturionHeadset] = {}

    def _refresh_interfaces(self) -> None:
        seen = {}
        for d in hid.enumerate(LOGITECH_VID):
            page, usage = d["usage_page"], d["usage"]
            if page in (0xFF00, 0xFF43) and (usage & 0xFF) == 0x02:
                seen[d["path"]] = ("hidpp", d["product_string"] or "")
            elif page == 0xFFA0:
                seen[d["path"]] = ("centurion", d["product_string"] or "")
        for path in list(self.interfaces):
            if path not in seen:
                del self.interfaces[path]  # unplugged
        for path, (proto, product) in seen.items():
            if path not in self.interfaces:
                self.interfaces[path] = (HidppInterface(path, product) if proto == "hidpp"
                                         else CenturionHeadset(path, product))

    def read_all(self) -> list[Reading]:
        """One reading per physical device, sorted for display."""
        self._refresh_interfaces()
        best: dict[str, Reading] = {}
        for iface in list(self.interfaces.values()):
            for r in iface.readings():
                cur = best.get(r.key)
                # prefer an online reading, then the direct (cable/own dongle) one
                if cur is None or (r.online, r.direct) > (cur.online, cur.direct):
                    best[r.key] = r
        return sorted(best.values(), key=lambda r: (KIND_ORDER.get(r.kind, 9), r.name))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    s = Scanner()
    for r in s.read_all():
        print(r)
