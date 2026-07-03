# PROTOCOL.md — the VIA wire protocol Emberglow speaks

This is the reference for the bytes Emberglow puts on the wire and for porting it
to another VIA/QMK board. The authoritative implementation is
[`emberglow/keyboard.py`](../emberglow/keyboard.py); every constant and function
named here lives there.

> Close the VIA app/web tab before doing any of this. The raw-HID interface is a
> single-holder resource — only one process can open it at a time.

> **Read this first.** The VIA lighting protocol has **two incompatible
> dialects**, and the Q10 speaks the *older* one (**v2**). Emberglow always
> handshakes the protocol version and picks the framing at runtime — it never
> assumes. See *The two VIA lighting dialects* and *The dialect trap* below; they
> are the whole point of this document.

## Device identity

Emberglow only ever opens one specific USB HID interface:

| What | Value | Constant (`keyboard.py`) |
|---|---|---|
| Vendor ID | `0x3434` (Keychron) | `VID` |
| Product ID | `0x01A1` (Q10) | `PID` |
| Usage page | `0xFF60` (QMK raw HID) | `USAGE_PAGE` |
| Usage | `0x61` | `USAGE` |

The Q10 enumerates as **several** HID interfaces off the same VID/PID — the
keyboard itself, media/consumer controls, a mouse endpoint, and the QMK raw-HID
endpoint. Only the last one accepts VIA packets, and it is uniquely identified by
`usage_page == 0xFF60 && usage == 0x61`. That is exactly the filter
`open_device()` applies:

```python
for d in hid.enumerate(VID, PID):
    if d["usage_page"] == USAGE_PAGE and d["usage"] == USAGE:
        dev = hid.device()
        dev.open_path(d["path"])
        return dev
raise KeyboardNotFound(...)
```

Run `emberglow enumerate` to see every interface the board exposes; the QMK
raw-HID line is the one matching `0xFF60`/`0x61`. If that line is absent, the
firmware isn't routing raw HID (see *Troubleshooting*).

## Raw-HID framing

Every exchange is a fixed **32-byte** payload (`REPORT_LEN = 32`). A VIA request
is a short command prefix, and the rest of the 32 bytes are zero-padded. On
Windows, hidapi requires a **leading `0x00` report-ID byte** prepended to the
write buffer (the report has no numbered report ID, so the ID is 0). The reply is
read back as up to `REPORT_LEN` bytes and **mirrors the request**: it echoes the
command bytes, followed by any returned data.

This is the entire low-level transport, `xfer`:

```python
def xfer(dev, payload, expect_reply=True):
    buf = payload + [0x00] * (REPORT_LEN - len(payload))
    dev.write([0x00] + buf)                  # leading 0x00 report ID (Windows)
    if not expect_reply:
        return []
    return dev.read(REPORT_LEN, timeout_ms=500) or []
```

Note the write is `[0x00] + buf` — 33 bytes go to `write()`, but the VIA payload
itself is 32. The read returns `[]` on timeout, which callers must tolerate.

## The command set (same bytes in both dialects)

These command bytes are identical no matter which lighting dialect the board
speaks (reference values from QMK's `quantum/via.h`):

| Command | Value | Constant | Purpose |
|---|---|---|---|
| Get protocol version | `0x01` | `CMD_GET_PROTOCOL_VERSION` | version / capability detection |
| Set value | `0x07` | `CMD_SET_VALUE` | write a lighting value |
| Get value | `0x08` | `CMD_GET_VALUE` | read a lighting value |
| Save | `0x09` | `CMD_SAVE` | persist to EEPROM |

What *differs* between dialects is the framing that follows `0x07`/`0x08`/`0x09`
and the value IDs used for brightness/effect/speed/color.

## The two VIA lighting dialects

This is the centerpiece. The `Dialect` class picks one based on the protocol
version: **v3 when `version >= V3_MIN_VERSION` (11), else v2.**

| | v3 (custom-channel) | v2 (lighting) |
|---|---|---|
| Protocol version | `>= 11` | `<= 10` |
| Set framing | `[0x07, CH_RGB_MATRIX, value_id, data…]` | `[0x07, value_id, data…]` |
| Get framing | `[0x08, CH_RGB_MATRIX, value_id]` | `[0x08, value_id]` |
| Channel byte | `CH_RGB_MATRIX = 3` present | **omitted** |
| Value IDs | `1–4` | `0x80–0x83` |
| Reply data offset | `3` | `2` |
| Save framing | `[0x09, CH_RGB_MATRIX]` | `[0x09]` |
| **The Q10 speaks** | | **this one (reports version 10)** |

The v2 framing simply drops the channel byte, which also shifts the reply data
one byte earlier (offset `2` instead of `3`) because the echoed header is one byte
shorter.

Value IDs for each dialect (`_V3_VALUE_IDS` / `_V2_VALUE_IDS` in `keyboard.py`):

| Value | v3 ID | v2 ID | Data bytes |
|---|---|---|---|
| Brightness | `1` | `0x80` | `[brightness 0-255]` |
| Effect | `2` | `0x81` | `[effect index]` |
| Speed | `3` | `0x82` | `[speed 0-255]` |
| Color | `4` | `0x83` | `[hue 0-255, sat 0-255]` |

**Hue and saturation are `0-255`, not `0-360`/`0-100`.** VIA scales the full
color wheel into a single byte, so red ≈ `0`, green ≈ `85`, blue ≈ `170`.

The `Dialect` class hides all of this behind logical value names:

```python
class Dialect:
    def __init__(self, version):
        self.v3 = version >= V3_MIN_VERSION       # 11
        self._ids = _V3_VALUE_IDS if self.v3 else _V2_VALUE_IDS

    def _set_payload(self, value, data):
        vid = self._ids[value]
        if self.v3:
            return [CMD_SET_VALUE, CH_RGB_MATRIX, vid, *data]
        return [CMD_SET_VALUE, vid, *data]        # v2: no channel byte

    def _data_offset(self):
        return 3 if self.v3 else 2                # reply echoes the header
```

`snapshot(dev, dialect)` reads effect/speed/brightness/color; `apply_snapshot`
writes them back (color, then speed, then brightness, then effect). Emberglow
saves a snapshot on first takeover so `done` can restore your real lighting.

## The dialect trap

Both dialects use command byte `0x07`, and the firmware **echoes every packet
back even when it ignores it**. So sending **v3 packets to a v2 board looks
perfect** — clean echo, no error — while doing **nothing**, and reads return `0`
on a board that is visibly lit. There is no error to catch; the only reliable
signal is the protocol version.

The two telltales that cracked this: reads were `[0]` on a glowing keyboard
(brightness can't be 0 on a lit board), and the version line said `10` while v3
would report `11+`.

Emberglow defends against this with a two-step handshake that runs before any
lighting command:

- **`detect_dialect(dev)`** sends `CMD_GET_PROTOCOL_VERSION` (`0x01`) — the one
  packet whose framing is identical across every VIA version, so it's safe to
  send before the dialect is known — then constructs `Dialect(version)` (v3 iff
  `version >= 11`).
- **`_verify(dev, dialect)`** does one real `get` and confirms the reply's leading
  bytes echo the request header. If they don't, it raises `ProtocolError` rather
  than silently no-op'ing in the wrong dialect.

```python
def detect_dialect(dev):
    version = protocol_version(dev)
    if version < 0:
        raise ProtocolError("no reply to the VIA protocol-version query (0x01)")
    dialect = Dialect(version)
    _verify(dev, dialect)
    return dialect
```

**The Q10 is v2.** The original `relay.py` POC is v3-only and does **not** work on
it — it emits `0x07, channel, …` packets the Q10 echoes but ignores. It's kept
for reference only. Do not reintroduce hardcoded v3 framing; that is the exact bug
this handshake exists to prevent.

## Version / capability detection

`protocol_version(dev)` is how you find out what a board speaks **before** you try
to drive it:

```python
def protocol_version(dev):
    ver = xfer(dev, [CMD_GET_PROTOCOL_VERSION])
    return (ver[1] << 8 | ver[2]) if len(ver) >= 3 else -1
```

It sends `CMD_GET_PROTOCOL_VERSION` (`0x01`) and decodes reply **bytes 1-2 as a
big-endian 16-bit integer** (`ver[1] << 8 | ver[2]`). It returns `-1` if the reply
is empty or too short.

Two things this is and isn't:

- It is the **VIA _protocol_ version** — which VIA command set the firmware
  speaks, and therefore which lighting dialect (`<= 10` → v2, `>= 11` → v3).
- It is **not** a firmware/product/build version. A matching protocol version is
  **necessary but not sufficient**: the firmware must also actually implement the
  lighting value set you're addressing. A board can answer the version query yet
  ignore the lighting commands if it was built without them.

`emberglow status` prints the **protocol version AND the chosen dialect**
alongside the current effect/speed/brightness/color, so it doubles as a quick
"which dialect does this board speak, and does it answer?" check.

## Effect indices

Effect **indices are firmware-specific**. VIA doesn't define fixed numbers for
"breathing" or "solid"; the index is just the position of that effect in the VIA
lighting dropdown, which is generated from **your firmware's enabled-effects
list**. Two boards — or two builds of the same board — can put breathing at
different indices.

Because of that, Emberglow doesn't hard-code them. It reads two env vars:

```python
BREATHING_EFFECT = int(os.environ.get("KB_BREATHING_EFFECT", "2"))
SOLID_EFFECT     = int(os.environ.get("KB_SOLID_EFFECT", "1"))
```

The confirmed value on this Q10 is **`KB_SOLID_EFFECT=1`**.

Discover yours with `emberglow probe`. It cycles through effect indices a couple
of seconds each so you can watch which one breathes and which is a plain solid
fill, then restores your original lighting. Set `KB_BREATHING_EFFECT` /
`KB_SOLID_EFFECT` (or edit the constants) to the indices you observed.

> **The invisible-probe lesson:** changing *only* the effect index is nearly
> invisible against whatever the board was already showing. So `emberglow probe`
> forces **full-brightness red** on every step, making each effect change
> unmistakable.

## Porting to another VIA/QMK board

Only a few values are board-specific. To port:

1. **Find the USB identity and raw-HID interface.** Run `emberglow enumerate`
   with the target board plugged in (wired). Note its `vendor_id`, `product_id`,
   and the `usage_page`/`usage` on the QMK raw-HID line. Most QMK boards use
   `0xFF60`/`0x61`, but confirm — some differ. Update `VID`, `PID`, `USAGE_PAGE`,
   `USAGE` at the top of [`emberglow/keyboard.py`](../emberglow/keyboard.py).
2. **See the version and dialect.** Run `emberglow status`. A valid version (not
   `-1`) means raw HID works; the printed dialect (v2/v3) tells you which framing
   Emberglow selected. `detect_dialect()` handles this automatically — you do not
   pick the dialect by hand.
3. **Probe effect indices.** Run `emberglow probe`, then set `KB_BREATHING_EFFECT`
   / `KB_SOLID_EFFECT` to the indices you observed.

Everything a port touches lives in one file,
[`emberglow/keyboard.py`](../emberglow/keyboard.py): identity constants, the
command bytes, the **`Dialect` class** (where the two framings and both value-ID
tables live), and the effect-index env vars.

## Troubleshooting

- **Nothing opens / `KeyboardNotFound`.** The **VIA app or web tab must be
  closed**. Raw HID is single-holder; if VIA has the interface, Emberglow can't.
- **`emberglow enumerate` shows no raw-HID line.** The board must be on **wired
  USB** — raw HID is not exposed over Bluetooth or the 2.4 GHz dongle. If it's
  wired and still absent, the firmware was built without raw HID (`RAW_ENABLE=yes`).
- **Writes echo cleanly but nothing changes, and reads are `0` on a lit board.**
  You are speaking the **wrong dialect**. Check the protocol version with
  `emberglow status`: `<= 10` means the board is v2 (like the Q10), `>= 11` means
  v3. This is the dialect trap — the firmware echoes packets it ignores, so a
  wrong-dialect write produces no error.
- **Reads come back empty or short.** `xfer` returns `[]` on a 500 ms read
  timeout, and `Dialect.get` guards against short replies (it returns zero-filled
  data rather than indexing past the end). `protocol_version` returns `-1` on an
  empty/short reply. Treat these as "board not answering," not a crash.
- **Colors look wrong.** Remember hue/sat are `0-255`, not degrees/percent.
