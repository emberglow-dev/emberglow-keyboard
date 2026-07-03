#!/usr/bin/env python3
"""
kb.py - Keychron Q10 lighting control via VIA protocol v2 (confirmed working).

Claude Code hooks:
    Notification -> python kb.py attention   (breathing red until you return)
    Stop         -> python kb.py done        (green flash, then restore)

Other commands:
    kb.py status        Show current effect/brightness/color
    kb.py probe [N M]   Cycle effect indices N..M (default 0..40) to find breathing
    kb.py effect N      Set effect index N
    kb.py restore       Restore saved pre-attention state
    kb.py test          attention -> wait -> done, end to end

Requires: pip install hidapi.  Close the VIA app/tab while using this.
"""

import json
import os
import sys
import time

import hid

# ---- Device (confirmed by enumeration) --------------------------------------
VID, PID = 0x3434, 0x01A1
USAGE_PAGE, USAGE = 0xFF60, 0x61
REPORT_LEN = 32

# ---- VIA protocol v2 lighting (confirmed by diag on this board) --------------
CMD_SET  = 0x07     # id_lighting_set_value
CMD_GET  = 0x08     # id_lighting_get_value
CMD_SAVE = 0x09     # id_lighting_save

V_BRIGHTNESS = 0x80
V_EFFECT     = 0x81
V_SPEED      = 0x82
V_COLOR      = 0x83     # data: hue, sat

# Set after `kb.py probe` finds the breathing effect on your firmware.
BREATHING_EFFECT = int(os.environ.get("KB_BREATHING_EFFECT", "4"))
SOLID_EFFECT     = int(os.environ.get("KB_SOLID_EFFECT", "1"))

HUE_RED, HUE_GREEN, SAT_FULL = 0, 85, 255
STATE_FILE = os.path.join(os.path.expanduser("~"), ".kb_q10_state.json")


# ---- HID plumbing ------------------------------------------------------------

def open_kb() -> hid.device:
    for d in hid.enumerate(VID, PID):
        if d["usage_page"] == USAGE_PAGE and d["usage"] == USAGE:
            dev = hid.device()
            dev.open_path(d["path"])
            return dev
    sys.exit("Q10 raw HID interface not found (keyboard unplugged, or VIA is open?)")


def xfer(dev, payload, expect_reply=True):
    buf = payload + [0x00] * (REPORT_LEN - len(payload))
    dev.write([0x00] + buf)          # leading 0x00 report ID (Windows)
    if not expect_reply:
        return []
    return dev.read(REPORT_LEN, timeout_ms=500) or []


def set_value(dev, value_id, *data):
    xfer(dev, [CMD_SET, value_id, *data])


def get_value(dev, value_id, n):
    reply = xfer(dev, [CMD_GET, value_id])
    # v2 reply: [cmd, value_id, data...] -> data starts at offset 2
    return list(reply[2:2 + n]) if len(reply) >= 2 + n else [0] * n


def save_to_eeprom(dev):
    xfer(dev, [CMD_SAVE], expect_reply=False)


# ---- Snapshot / restore --------------------------------------------------------

def snapshot(dev) -> dict:
    return {
        "effect":     get_value(dev, V_EFFECT, 1)[0],
        "speed":      get_value(dev, V_SPEED, 1)[0],
        "brightness": get_value(dev, V_BRIGHTNESS, 1)[0],
        "color":      get_value(dev, V_COLOR, 2),
    }


def apply_state(dev, st):
    set_value(dev, V_COLOR, *st["color"])
    set_value(dev, V_SPEED, st["speed"])
    set_value(dev, V_BRIGHTNESS, st["brightness"])
    set_value(dev, V_EFFECT, st["effect"])


def save_snapshot_once(dev):
    """Snapshot only if none exists, so repeated Notification events don't
    overwrite the user's real state with our red-breathing state."""
    if not os.path.exists(STATE_FILE):
        with open(STATE_FILE, "w") as f:
            json.dump(snapshot(dev), f)


def restore_snapshot(dev) -> bool:
    if not os.path.exists(STATE_FILE):
        return False
    with open(STATE_FILE) as f:
        apply_state(dev, json.load(f))
    os.remove(STATE_FILE)
    return True


# ---- Commands -------------------------------------------------------------------

def cmd_attention(dev):
    save_snapshot_once(dev)
    set_value(dev, V_COLOR, HUE_RED, SAT_FULL)
    set_value(dev, V_BRIGHTNESS, 255)
    set_value(dev, V_SPEED, 128)
    set_value(dev, V_EFFECT, BREATHING_EFFECT)


def cmd_done(dev):
    set_value(dev, V_COLOR, HUE_GREEN, SAT_FULL)
    set_value(dev, V_BRIGHTNESS, 255)
    set_value(dev, V_EFFECT, SOLID_EFFECT)
    time.sleep(1.2)
    if not restore_snapshot(dev):
        set_value(dev, V_EFFECT, SOLID_EFFECT)
        set_value(dev, V_COLOR, 170, 255)   # sane blue fallback


def cmd_status(dev):
    st = snapshot(dev)
    print(f"effect={st['effect']} speed={st['speed']} "
          f"brightness={st['brightness']} hue/sat={st['color']}")


def cmd_probe(dev, start=0, end=40):
    original = snapshot(dev)
    print(f"Your normal effect is #{original['effect']}. "
          f"Cycling {start}..{end}, 2s each; note which one breathes. Ctrl+C to stop.")
    set_value(dev, V_BRIGHTNESS, 255)
    set_value(dev, V_COLOR, HUE_RED, SAT_FULL)
    try:
        for i in range(start, end + 1):
            print(f"  effect index {i}")
            set_value(dev, V_EFFECT, i)
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        apply_state(dev, original)
        print("Restored. Set KB_BREATHING_EFFECT=<index> or edit the constant.")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    dev = open_kb()
    try:
        if cmd == "attention":
            cmd_attention(dev)
        elif cmd == "done":
            cmd_done(dev)
        elif cmd == "restore":
            restore_snapshot(dev)
        elif cmd == "status":
            cmd_status(dev)
        elif cmd == "probe":
            cmd_probe(dev, *(int(a) for a in args[:2]))
        elif cmd == "effect" and args:
            set_value(dev, V_EFFECT, int(args[0]))
        elif cmd == "test":
            print("attention (breathing red)...")
            cmd_attention(dev)
            time.sleep(4)
            print("done (green flash + restore)...")
            cmd_done(dev)
        else:
            sys.exit(__doc__)
    finally:
        dev.close()


if __name__ == "__main__":
    main()