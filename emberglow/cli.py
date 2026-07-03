"""``emberglow`` command-line interface.

    emberglow set <state>     apply a lighting state (working|needsyou|done|failed)
    emberglow restore         restore the pre-takeover lighting
    emberglow status          print VIA protocol version + current lighting
    emberglow probe           cycle effect indices to find "breathing"
    emberglow enumerate       list HID interfaces (debugging)
    emberglow serve           run the webhook server

The ``set`` / ``restore`` commands are what Claude Code hooks call; ``serve`` is
what Anthropic Managed Agents webhooks hit. See README.md and CLAUDE.md.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from . import __version__
from .keyboard import Keyboard, KeyboardNotFound
from .states import STATES


def _cmd_set(args) -> int:
    Keyboard().apply(args.state)
    print(f"applied {args.state}")
    return 0


def _cmd_restore(args) -> int:
    restored = Keyboard().restore()
    print("restored previous lighting" if restored else "no saved state to restore")
    return 0


def _cmd_status(args) -> int:
    st = Keyboard().status()
    print(f"VIA protocol version: {st['protocol_version']}  (dialect: {st['dialect']})")
    print(
        f"effect={st['effect']} speed={st['speed']} "
        f"brightness={st['brightness']} hue/sat={st['color']}"
    )
    return 0


def _cmd_probe(args) -> int:
    from . import keyboard as kbmod

    HUE_RED = 0  # vivid, unmistakable color so every effect change is obvious
    print(f"Cycling effect indices 0-{args.count - 1}, {args.hold}s each,")
    print("forcing full-brightness RED so each effect is clearly visible.")
    print("Note the index that is SOLID (steady) → KB_SOLID_EFFECT, and the one")
    print("that BREATHES → KB_BREATHING_EFFECT.  Ctrl+C to stop early.\n")
    dev = kbmod.open_device()
    try:
        dialect = kbmod.detect_dialect(dev)
        print(f"(protocol {dialect.version}, {dialect.name} framing)\n")
        original = kbmod.snapshot(dev, dialect)
        try:
            for i in range(args.count):
                print(f"  effect index {i}")
                dialect.set(dev, "brightness", 255)
                dialect.set(dev, "color", HUE_RED, kbmod.SAT_FULL)
                dialect.set(dev, "effect", i)
                time.sleep(args.hold)
        except KeyboardInterrupt:
            pass
        finally:
            kbmod.apply_snapshot(dev, dialect, original)
            print("\nRestored original lighting.")
    finally:
        dev.close()
    return 0


def _cmd_enumerate(args) -> int:
    import hid

    from .keyboard import USAGE, USAGE_PAGE, VID

    devices = [d for d in hid.enumerate() if d["vendor_id"] == VID]
    if not devices:
        print(f"No Keychron (VID 0x{VID:04X}) HID interfaces found.")
        print("If on Bluetooth/2.4GHz, switch the board to WIRED and retry.")
        return 1
    print(f"Found {len(devices)} Keychron HID interface(s):\n")
    for d in devices:
        raw = d["usage_page"] == USAGE_PAGE and d["usage"] == USAGE
        tag = "  <-- QMK raw-HID interface (this is the one we use)" if raw else ""
        print(
            f"  PID=0x{d['product_id']:04X}  usage_page=0x{d['usage_page']:04X}  "
            f"usage=0x{d['usage']:02X}  product={d.get('product_string')!r}{tag}"
        )
    return 0


def _cmd_serve(args) -> int:
    from .server import create_app_from_env

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not os.environ.get("ANTHROPIC_WEBHOOK_SIGNING_KEY") and not (
        os.environ.get("KB_ALLOW_UNVERIFIED") == "1"
    ):
        print("WARNING: ANTHROPIC_WEBHOOK_SIGNING_KEY is not set — real webhooks")
        print("will be rejected. For local testing, set KB_ALLOW_UNVERIFIED=1.\n")
    app = create_app_from_env()
    print(f"emberglow listening on http://{args.host}:{args.port}  (POST /webhook)")
    app.run(host=args.host, port=args.port, threaded=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="emberglow", description=__doc__.splitlines()[0])
    p.add_argument("--version", action="version", version=f"emberglow {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("set", help="apply a lighting state")
    s.add_argument("state", choices=sorted(STATES))
    s.set_defaults(func=_cmd_set)

    sub.add_parser("restore", help="restore pre-takeover lighting").set_defaults(
        func=_cmd_restore
    )
    sub.add_parser("status", help="print protocol version + current lighting").set_defaults(
        func=_cmd_status
    )

    pr = sub.add_parser("probe", help="cycle effect indices to find breathing")
    pr.add_argument("--count", type=int, default=15)
    pr.add_argument("--hold", type=float, default=2.0)
    pr.set_defaults(func=_cmd_probe)

    sub.add_parser("enumerate", help="list Keychron HID interfaces").set_defaults(
        func=_cmd_enumerate
    )

    sv = sub.add_parser("serve", help="run the webhook server")
    sv.add_argument("--host", default="0.0.0.0")
    sv.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8787")))
    sv.set_defaults(func=_cmd_serve)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
