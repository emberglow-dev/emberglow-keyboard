"""Unit tests for the Keyboard driver, using a fake HID device (no hardware)."""

import json

import pytest

from emberglow import keyboard as kbmod
from emberglow.states import HUE_BLUE, HUE_GREEN, HUE_ORANGE

from conftest import FakeHidDevice


def test_apply_working_uses_breathing_blue_and_closes(keyboard, fake_device):
    keyboard.apply("working")
    assert fake_device.last_color() == (HUE_BLUE, kbmod.SAT_FULL)
    assert fake_device.last_effect() == kbmod.BREATHING_EFFECT
    assert fake_device.closed is True


def test_apply_needsyou_uses_breathing_orange(keyboard, fake_device):
    keyboard.apply("needsyou")
    assert fake_device.last_color() == (HUE_ORANGE, kbmod.SAT_FULL)
    assert fake_device.last_effect() == kbmod.BREATHING_EFFECT


def test_unknown_state_raises(keyboard):
    with pytest.raises(ValueError):
        keyboard.apply("chartreuse")


def test_snapshot_saved_once_then_not_overwritten(fake_device, tmp_path):
    state_file = tmp_path / "state.json"
    kb = kbmod.Keyboard(
        open_device=lambda: fake_device, state_file=str(state_file), sleep=lambda _s: None
    )
    kb.apply("working")
    assert state_file.exists(), "first takeover should snapshot the prior lighting"
    mtime = state_file.stat().st_mtime_ns
    kb.apply("failed")  # second takeover must NOT overwrite the saved snapshot
    assert state_file.stat().st_mtime_ns == mtime


def test_done_flashes_green_then_restores(fake_device, tmp_path):
    state_file = tmp_path / "state.json"
    # Pretend a snapshot exists from an earlier takeover.
    saved = {"effect": 3, "speed": 100, "brightness": 200, "color": [200, 255]}
    state_file.write_text(json.dumps(saved))

    slept = []
    kb = kbmod.Keyboard(
        open_device=lambda: fake_device,
        state_file=str(state_file),
        sleep=lambda s: slept.append(s),
    )
    kb.apply("done")

    # It flashed green...
    colors = [tuple(data[:2]) for vid, data in fake_device.set_values
              if vid == kbmod._V2_VALUE_IDS["color"]]
    assert (HUE_GREEN, kbmod.SAT_FULL) in colors
    # ...held for the configured time...
    assert slept == [kbmod.DONE_HOLD_SECONDS]
    # ...then restored the saved color and removed the snapshot file.
    assert fake_device.last_color() == (200, 255)
    assert not state_file.exists()


def test_restore_without_snapshot_is_a_noop(fake_device, tmp_path):
    kb = kbmod.Keyboard(
        open_device=lambda: fake_device, state_file=str(tmp_path / "nope.json")
    )
    assert kb.restore() is False


def test_open_device_missing_raises_keyboard_not_found(monkeypatch):
    class _FakeHid:
        @staticmethod
        def enumerate(vid, pid):
            return []  # nothing connected

    monkeypatch.setitem(__import__("sys").modules, "hid", _FakeHid)
    with pytest.raises(kbmod.KeyboardNotFound):
        kbmod.open_device()


def test_protocol_version_decodes_big_endian(fake_device, monkeypatch):
    # Reply to CMD_GET_PROTOCOL_VERSION: [cmd, hi, lo, ...] -> hi<<8 | lo
    monkeypatch.setattr(
        fake_device, "read", lambda size, timeout_ms=500: [0x01, 0x00, 0x09] + [0] * 29
    )
    assert kbmod.protocol_version(fake_device) == 9


# ---- Protocol dialect detection (the v2/v3 bug that bit us) ------------------

def test_detect_dialect_v2_for_q10_version_10():
    d = kbmod.detect_dialect(FakeHidDevice(version=10))
    assert d.name == "v2"
    assert d.v3 is False


def test_detect_dialect_v3_for_version_11():
    d = kbmod.detect_dialect(FakeHidDevice(version=11))
    assert d.name == "v3"
    assert d.v3 is True


def test_v2_set_omits_channel_byte_and_uses_0x80_ids():
    dev = FakeHidDevice(version=10)
    d = kbmod.detect_dialect(dev)
    dev.writes.clear()
    d.set(dev, "color", HUE_GREEN, kbmod.SAT_FULL)
    w = dev.writes[-1]  # [0x00, 0x07, 0x83, hue, sat, ...]  — NO channel byte
    assert w[1] == kbmod.CMD_SET_VALUE
    assert w[2] == kbmod._V2_VALUE_IDS["color"]  # 0x83
    assert w[3:5] == [HUE_GREEN, kbmod.SAT_FULL]


def test_v3_set_includes_channel_byte_and_uses_1_4_ids():
    dev = FakeHidDevice(version=11)
    d = kbmod.detect_dialect(dev)
    dev.writes.clear()
    d.set(dev, "color", HUE_GREEN, kbmod.SAT_FULL)
    w = dev.writes[-1]  # [0x00, 0x07, CH, 4, hue, sat, ...]
    assert w[1] == kbmod.CMD_SET_VALUE
    assert w[2] == kbmod.CH_RGB_MATRIX
    assert w[3] == kbmod._V3_VALUE_IDS["color"]  # 4
    assert w[4:6] == [HUE_GREEN, kbmod.SAT_FULL]


def test_detect_dialect_raises_when_no_version_reply(monkeypatch):
    dev = FakeHidDevice()
    monkeypatch.setattr(dev, "read", lambda size, timeout_ms=500: [])
    with pytest.raises(kbmod.ProtocolError):
        kbmod.detect_dialect(dev)
