"""Shared test fixtures: a fake HID device, a signing helper, a recording keyboard.

No physical keyboard is required for the default test run. Hardware tests are
marked ``@pytest.mark.hardware`` and skipped unless you run ``pytest -m hardware``.
"""

from __future__ import annotations

import base64
import datetime
import json
import time
from datetime import timezone

import pytest

from emberglow import keyboard as kbmod


class FakeHidDevice:
    """Mimics the Q10's raw-HID behavior: replies to the version query and
    **echoes every other request's header back** (with zeroed data) — exactly
    the "polite listener" behavior that hid the v2/v3 dialect bug.

    ``version`` controls which dialect ``detect_dialect`` picks (Q10 = 10 = v2).
    ``writes`` holds raw byte lists sent to ``write`` (leading 0x00 report ID
    included); ``set_values`` decodes them into ``(value_id, data)`` tuples using
    the dialect implied by ``version``.
    """

    def __init__(self, version: int = 10):
        self.version = version
        self.writes: list[list[int]] = []
        self.closed = False
        self._last: list[int] = []

    def write(self, data):
        self.writes.append(list(data))
        self._last = list(data)[1:]  # strip the leading 0x00 report ID
        return len(data)

    def read(self, size, timeout_ms=500):
        req = self._last
        if req and req[0] == kbmod.CMD_GET_PROTOCOL_VERSION:
            hi, lo = (self.version >> 8) & 0xFF, self.version & 0xFF
            return [kbmod.CMD_GET_PROTOCOL_VERSION, hi, lo] + [0] * (kbmod.REPORT_LEN - 3)
        # Echo the request header, then zeroed data — like the real firmware.
        return (req + [0] * kbmod.REPORT_LEN)[: kbmod.REPORT_LEN]

    def close(self):
        self.closed = True

    # -- assertion helpers (dialect-aware) -------------------------------------

    @property
    def _v3(self) -> bool:
        return self.version >= kbmod.V3_MIN_VERSION

    @property
    def set_values(self) -> list[tuple[int, list[int]]]:
        out = []
        for w in self.writes:
            if len(w) >= 3 and w[1] == kbmod.CMD_SET_VALUE:
                if self._v3:
                    out.append((w[3], w[4:]))   # [0x00, 0x07, CH, vid, *data]
                else:
                    out.append((w[2], w[3:]))    # [0x00, 0x07, vid, *data]
        return out

    def _last_value(self, value: str):
        vid = (kbmod._V3_VALUE_IDS if self._v3 else kbmod._V2_VALUE_IDS)[value]
        for got_vid, data in reversed(self.set_values):
            if got_vid == vid:
                return data
        return None

    def last_color(self):
        data = self._last_value("color")
        return (data[0], data[1]) if data else None

    def last_effect(self):
        data = self._last_value("effect")
        return data[0] if data else None


@pytest.fixture
def fake_device():
    return FakeHidDevice()  # version 10 -> v2, like the real Q10


@pytest.fixture
def keyboard(fake_device, tmp_path):
    """A Keyboard wired to the fake device, an isolated state file, no real sleep."""
    return kbmod.Keyboard(
        open_device=lambda: fake_device,
        state_file=str(tmp_path / "state.json"),
        sleep=lambda _s: None,
    )


class RecordingKeyboard:
    """Stand-in for the server's keyboard: records applied states."""

    def __init__(self, raises=None):
        self.applied: list[str] = []
        self._raises = raises

    def apply(self, state):
        if self._raises is not None:
            raise self._raises
        self.applied.append(state)


# ---- Webhook signing helper --------------------------------------------------

TEST_SIGNING_KEY = "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()


def sign_webhook(event_type: str, event_id: str = "event_test", *, key: str = TEST_SIGNING_KEY):
    """Return (payload_str, headers) for a validly-signed webhook of ``event_type``."""
    from standardwebhooks import Webhook

    payload = json.dumps(
        {
            "type": "event",
            "id": event_id,
            "created_at": "2026-07-03T00:00:00Z",
            "data": {"type": event_type, "id": "sesn_test"},
        }
    )
    ts = int(time.time())
    sig = Webhook(key).sign(
        event_id, datetime.datetime.fromtimestamp(ts, tz=timezone.utc), payload
    )
    headers = {
        "webhook-id": event_id,
        "webhook-timestamp": str(ts),
        "webhook-signature": sig,
        "content-type": "application/json",
    }
    return payload, headers
