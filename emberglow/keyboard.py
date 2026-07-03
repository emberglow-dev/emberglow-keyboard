"""Keychron Q10 lighting control over the VIA protocol (QMK raw HID).

This is the one place that talks to hardware.

⚠️ HARD-WON KNOWLEDGE — the VIA lighting protocol has two incompatible dialects,
and the Q10 does NOT speak the newer one. ALWAYS handshake the version first.

* VIA **v3** (protocol version >= 11): custom-channel framing
      set = [0x07, channel, value_id, data...]   value_ids 1..4
* VIA **v2** (protocol version <= 10, e.g. the Q10 reports 10): lighting framing
      set = [0x07, value_id, data...]            value_ids 0x80..0x83   (NO channel byte)

Both commands share the byte 0x07, and the firmware **echoes every packet back
even when it ignores it** — so sending v3 packets to a v2 board looks like it
worked (clean echo, no error) while doing nothing, and reads return zeros on a
board that's visibly lit. The only reliable signal is the protocol version.
`detect_dialect()` reads version command 0x01 (identical in every VIA version),
picks the framing, and `_verify()` confirms the reply echoes the request header.

Close the VIA app/tab while using this — only one process can hold the raw-HID
interface at a time.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Callable, Protocol

from .states import STATES

# ---- Device identity (confirmed by enumeration on the Q10) -------------------
VID = 0x3434
PID = 0x01A1
USAGE_PAGE = 0xFF60   # QMK raw HID
USAGE = 0x61

# ---- Command IDs (same byte in both VIA dialects) ----------------------------
CMD_GET_PROTOCOL_VERSION = 0x01
CMD_SET_VALUE = 0x07   # v3 id_custom_set_value / v2 id_lighting_set_value
CMD_GET_VALUE = 0x08   # v3 id_custom_get_value / v2 id_lighting_get_value
CMD_SAVE = 0x09        # v3 id_custom_save     / v2 id_lighting_save

CH_RGB_MATRIX = 3      # v3 only: id_qmk_rgb_matrix_channel

# Logical lighting values → the value-id each dialect uses for them.
_V3_VALUE_IDS = {"brightness": 1, "effect": 2, "speed": 3, "color": 4}
_V2_VALUE_IDS = {"brightness": 0x80, "effect": 0x81, "speed": 0x82, "color": 0x83}

# VIA v3 was introduced at protocol version 11. Anything lower speaks v2 framing.
V3_MIN_VERSION = 11

REPORT_LEN = 32            # VIA raw-HID report payload size
SAT_FULL = 255
DEFAULT_SPEED = 128
DONE_HOLD_SECONDS = 1.2

# Effect indices are firmware-specific — they match the VIA lighting dropdown
# order for YOUR enabled-effects list. Discover yours with ``emberglow probe``.
BREATHING_EFFECT = int(os.environ.get("KB_BREATHING_EFFECT", "2"))
SOLID_EFFECT = int(os.environ.get("KB_SOLID_EFFECT", "1"))

# Where the pre-takeover snapshot is stored so "done" can restore your lighting.
DEFAULT_STATE_FILE = os.path.join(os.path.expanduser("~"), ".emberglow_state.json")


class KeyboardNotFound(RuntimeError):
    """Raised when the Q10 raw-HID interface can't be opened."""


class ProtocolError(RuntimeError):
    """Raised when the firmware doesn't answer the version handshake sanely."""


class HidDevice(Protocol):
    """The slice of the hidapi device interface we use (also what fakes mock)."""

    def write(self, data: list[int]) -> int: ...
    def read(self, size: int, timeout_ms: int = ...) -> list[int]: ...
    def close(self) -> None: ...


def open_device() -> HidDevice:
    """Open the Q10's QMK raw-HID interface, or raise :class:`KeyboardNotFound`."""
    import hid

    for d in hid.enumerate(VID, PID):
        if d["usage_page"] == USAGE_PAGE and d["usage"] == USAGE:
            dev = hid.device()
            dev.open_path(d["path"])
            return dev
    raise KeyboardNotFound(
        "Q10 raw-HID interface not found. Is the keyboard plugged in (wired), "
        "and is the VIA app/tab closed? Only one process can hold the interface."
    )


# ---- Low-level VIA framing ---------------------------------------------------

def xfer(dev: HidDevice, payload: list[int], expect_reply: bool = True) -> list[int]:
    """Send one VIA packet. Windows requires a leading 0x00 report-ID byte."""
    buf = payload + [0x00] * (REPORT_LEN - len(payload))
    dev.write([0x00] + buf)
    if not expect_reply:
        return []
    return dev.read(REPORT_LEN, timeout_ms=500) or []


def protocol_version(dev: HidDevice) -> int:
    """Read the VIA protocol version (big-endian 16-bit) — the ONE packet that is
    identical across every VIA version, so it's safe to send before we know the
    dialect. The reply puts the version in bytes 1-2."""
    ver = xfer(dev, [CMD_GET_PROTOCOL_VERSION])
    return (ver[1] << 8 | ver[2]) if len(ver) >= 3 else -1


class Dialect:
    """Frames lighting packets for a specific VIA protocol version.

    Speaks v3 custom-channel framing when ``version >= 11``, v2 lighting framing
    (no channel byte, 0x80-series value IDs) otherwise. The Q10 is v2.
    """

    def __init__(self, version: int) -> None:
        self.version = version
        self.v3 = version >= V3_MIN_VERSION
        self._ids = _V3_VALUE_IDS if self.v3 else _V2_VALUE_IDS

    @property
    def name(self) -> str:
        return "v3" if self.v3 else "v2"

    def _set_payload(self, value: str, data: list[int]) -> list[int]:
        vid = self._ids[value]
        if self.v3:
            return [CMD_SET_VALUE, CH_RGB_MATRIX, vid, *data]
        return [CMD_SET_VALUE, vid, *data]

    def _get_payload(self, value: str) -> list[int]:
        vid = self._ids[value]
        if self.v3:
            return [CMD_GET_VALUE, CH_RGB_MATRIX, vid]
        return [CMD_GET_VALUE, vid]

    def _data_offset(self) -> int:
        # Reply echoes the request header; data follows it.
        return 3 if self.v3 else 2

    def set(self, dev: HidDevice, value: str, *data: int) -> None:
        xfer(dev, self._set_payload(value, list(data)))

    def get(self, dev: HidDevice, value: str, n: int) -> list[int]:
        reply = xfer(dev, self._get_payload(value))
        off = self._data_offset()
        return reply[off:off + n] if len(reply) >= off + n else [0] * n

    def save(self, dev: HidDevice) -> None:
        payload = [CMD_SAVE, CH_RGB_MATRIX] if self.v3 else [CMD_SAVE]
        xfer(dev, payload, expect_reply=False)


def detect_dialect(dev: HidDevice) -> Dialect:
    """Handshake the protocol version, choose the framing, verify the link.

    This runs before we drive any lighting so we never silently no-op by
    speaking the wrong dialect (the bug that cost us: v3 packets to a v2 board).
    """
    version = protocol_version(dev)
    if version < 0:
        raise ProtocolError("no reply to the VIA protocol-version query (0x01)")
    dialect = Dialect(version)
    _verify(dev, dialect)
    return dialect


def _verify(dev: HidDevice, dialect: Dialect) -> None:
    """Do one real read and confirm the reply echoes the request header."""
    request = dialect._get_payload("brightness")
    reply = xfer(dev, request)
    if reply[: len(request)] != request:
        raise ProtocolError(
            f"VIA link check failed: sent {request}, got {reply[:len(request)]!r}. "
            f"Firmware may not speak {dialect.name} lighting framing."
        )


def snapshot(dev: HidDevice, dialect: Dialect) -> dict:
    """Capture the current effect/speed/brightness/color so it can be restored."""
    return {
        "effect":     dialect.get(dev, "effect", 1)[0],
        "speed":      dialect.get(dev, "speed", 1)[0],
        "brightness": dialect.get(dev, "brightness", 1)[0],
        "color":      dialect.get(dev, "color", 2),
    }


def apply_snapshot(dev: HidDevice, dialect: Dialect, st: dict) -> None:
    dialect.set(dev, "color", *st["color"])
    dialect.set(dev, "speed", st["speed"])
    dialect.set(dev, "brightness", st["brightness"])
    dialect.set(dev, "effect", st["effect"])


class Keyboard:
    """Thread-safe front door for applying lighting states.

    Args:
        open_device: factory returning an :class:`HidDevice`. Override in tests.
        state_file:  where the pre-takeover snapshot is saved for "done".
        sleep:       injectable sleep (tests pass a no-op to skip the flash hold).
    """

    def __init__(
        self,
        open_device: Callable[[], HidDevice] = open_device,
        state_file: str = DEFAULT_STATE_FILE,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._open = open_device
        self._state_file = state_file
        self._sleep = sleep
        self._lock = threading.Lock()

    def apply(self, state_name: str) -> None:
        """Open the board, detect the dialect, apply ``state_name``, close."""
        if state_name not in STATES:
            raise ValueError(
                f"unknown state {state_name!r}; known: {sorted(STATES)}"
            )
        with self._lock:
            dev = self._open()
            try:
                dialect = detect_dialect(dev)
                self._apply(dev, dialect, state_name)
            finally:
                dev.close()

    def restore(self) -> bool:
        """Restore the saved pre-takeover lighting. Returns True if one existed."""
        with self._lock:
            dev = self._open()
            try:
                return self._restore(dev, detect_dialect(dev))
            finally:
                dev.close()

    def status(self) -> dict:
        """Return protocol version + dialect + current lighting."""
        with self._lock:
            dev = self._open()
            try:
                dialect = detect_dialect(dev)
                return {
                    "protocol_version": dialect.version,
                    "dialect": dialect.name,
                    **snapshot(dev, dialect),
                }
            finally:
                dev.close()

    # -- internals (assume the lock is held and dev is open) -------------------

    def _apply(self, dev: HidDevice, dialect: Dialect, state_name: str) -> None:
        state = STATES[state_name]
        if state_name == "done":
            dialect.set(dev, "color", state.hue, SAT_FULL)
            dialect.set(dev, "brightness", 255)
            dialect.set(dev, "effect", SOLID_EFFECT)
            self._sleep(DONE_HOLD_SECONDS)
            self._restore(dev, dialect)
            return

        # working / needsyou / failed: remember the user's real lighting once,
        # then take over the board.
        self._save_snapshot_once(dev, dialect)
        dialect.set(dev, "color", state.hue, SAT_FULL)
        dialect.set(dev, "brightness", 255)
        dialect.set(dev, "speed", DEFAULT_SPEED)
        dialect.set(dev, "effect", BREATHING_EFFECT if state.breathing else SOLID_EFFECT)

    def _save_snapshot_once(self, dev: HidDevice, dialect: Dialect) -> None:
        # Repeated events must not overwrite the user's real state with ours.
        if not os.path.exists(self._state_file):
            with open(self._state_file, "w") as f:
                json.dump(snapshot(dev, dialect), f)

    def _restore(self, dev: HidDevice, dialect: Dialect) -> bool:
        if not os.path.exists(self._state_file):
            return False
        with open(self._state_file) as f:
            apply_snapshot(dev, dialect, json.load(f))
        os.remove(self._state_file)
        return True
