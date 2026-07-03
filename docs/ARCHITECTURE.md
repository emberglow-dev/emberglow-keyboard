# Architecture

How Emberglow is put together, and why. Read [`README.md`](../README.md) for
what it does and [`CLAUDE.md`](../CLAUDE.md) for a working map of the repo; this
doc is for a contributor who wants to understand the design before changing it.

## High-level overview

Emberglow has **two front ends and one lighting engine**. Both front ends do the
same last thing: call `Keyboard.apply(state)`. Everything below that is a single
code path that frames VIA raw-HID packets and writes them to the Q10.

- The **CLI** (`emberglow/cli.py`) is what **Claude Code hooks** invoke —
  `emberglow set working`, `emberglow set done`, etc. One process per event: open
  the board, apply, close.
- The **Flask server** (`emberglow/server.py`) is what **Anthropic Managed Agents
  webhooks** hit. It verifies the request signature, maps the event to a state,
  and drives the same `Keyboard`.

```
  Claude Code hooks                       Anthropic webhooks (Managed Agents)
        │                                             │
        │  emberglow set <state>                      │  POST /webhook  (signed)
        ▼                                             ▼
  ┌───────────────┐                          ┌──────────────────────────────┐
  │  cli.py        │                         │  server.py  (create_app)       │
  │  argparse      │                         │  1. verify HMAC signature      │
  │  _cmd_set()    │                         │  2. de-dupe by event.id        │
  │                │                         │  3. state_for_event(data.type) │
  └───────┬────────┘                         └───────────────┬────────────────┘
          │                                                  │
          │            Keyboard.apply(state)                 │
          └────────────────────────┬─────────────────────────┘
                                    ▼
                     ┌──────────────────────────────┐
                     │  keyboard.py  (Keyboard)       │
                     │  lock → open_device()          │
                     │  _apply(): snapshot / restore  │
                     │  VIA raw-HID framing (xfer)    │
                     └───────────────┬────────────────┘
                                     ▼
                            Keychron Q10  (USB, QMK raw HID)
```

The one shared vocabulary between the two paths is `states.py`: it defines the
four looks (`STATES`) and the webhook-event → state map (`EVENT_STATE`).

## Layered modules and why they're split

The package is deliberately layered so that each concern lives in exactly one
place, and so that everything except the actual USB write can be tested without
hardware.

| Module | Responsibility | Depends on |
|---|---|---|
| `states.py` | **Pure data.** `LightingState`, `STATES`, `EVENT_STATE`, `state_for_event()`. No I/O, no hardware. | nothing |
| `keyboard.py` | **The only hardware-touching module.** VIA constants + framing (`xfer`, `set_value`, `get_value`, `snapshot`, …) and the `Keyboard` engine. | `states.py`, `hid` |
| `server.py` | **`create_app()` Flask factory.** Signature verification, de-dup, event mapping. | `keyboard.py`, `states.py`, `flask`, `anthropic` |
| `cli.py` | **argparse front end.** `set`/`restore`/`status`/`probe`/`enumerate`/`serve`. | `keyboard.py`, `states.py` |
| `__init__.py` | Public API + a **lazy** `create_app` re-export. | `states.py`, `keyboard.py` |

Why the split matters:

- **`states.py` is pure data** so you can add or retune a state, or change what a
  webhook does, by editing one file with no hardware and no mocking — and unit
  test it directly. "Change what a webhook does" == edit `EVENT_STATE`, full stop.
- **`keyboard.py` is the only module that talks to hardware.** Every byte that
  reaches the Q10 goes through `xfer()`. If you're changing VIA framing or adding
  a command, you only touch this file, and you keep functions taking an injectable
  `dev` so the fake device works.
- **`server.py` exposes a `create_app()` factory** rather than a module-level
  `app`, so tests (and `create_app_from_env()` for the real `serve`) construct it
  with the exact keyboard, signing key, and verification policy they want.
- **`cli.py` is a thin argparse shim** — each subcommand just constructs a
  `Keyboard()` and calls a method. No lighting logic lives here.

### Lazy `create_app` re-export

`import emberglow` must work for the CLI `set` path **without** Flask or the
`anthropic` SDK installed — a Claude Code hook shouldn't drag in a web stack.
So `__init__.py` re-exports `Keyboard`, `STATES`, etc. eagerly, but wraps
`create_app` in a function that imports `server` (and thus Flask/anthropic) only
when actually called:

```python
def create_app(*args, **kwargs):
    from .server import create_app as _create_app
    return _create_app(*args, **kwargs)
```

## Key design decisions

### Dependency injection in `Keyboard(...)`

`Keyboard.__init__` takes `open_device`, `state_file`, and `sleep` as arguments:

```python
Keyboard(open_device=open_device, state_file=DEFAULT_STATE_FILE, sleep=time.sleep)
```

In production these default to the real hidapi factory, `~/.emberglow_state.json`,
and `time.sleep`. In tests the `keyboard` fixture injects a `FakeHidDevice`
factory, a `tmp_path` state file, and a no-op `sleep` — so the whole engine runs
with no keyboard, no home-directory pollution, and no real 1.2s flash hold.

### `create_app(keyboard=..., signing_key=..., allow_unverified=...)` factory

The factory lets a test build the app around a `RecordingKeyboard` (which just
appends applied state names to a list) and the known `TEST_SIGNING_KEY`, so it can
POST a genuinely-signed body and then assert on `keyboard.applied`. The real
entry point uses the same factory via `create_app_from_env()`, which reads the
signing key and `KB_ALLOW_UNVERIFIED` from the environment.

### A `threading.Lock` around device access

Flask runs `app.run(..., threaded=True)`, so multiple webhook requests can land at
once. The raw-HID interface is single-holder — only one caller may talk to the
device at a time. `Keyboard._lock` serializes `apply`/`restore`/`status`, so
concurrent webhooks can't interleave VIA packets or open the device twice.

### Open-per-request device access

`apply()` calls `self._open()`, does its work in a `try`, and `close()`s in a
`finally` — every single time. It never holds the handle open between events.
This is robust against the board being unplugged and replugged: a stale handle
would fail on the next event, whereas open-per-request just reconnects (or raises
`KeyboardNotFound`, which the callers tolerate — see below).

### Snapshot / restore for the `done` state

`working`, `needsyou`, and `failed` **take over** the board. Before the first
takeover, `_save_snapshot_once()` reads the user's current effect/speed/
brightness/color and writes it to the state file. The `done` state is special-
cased in `_apply`: it flashes green, holds for `DONE_HOLD_SECONDS`, then calls
`_restore(dev)` to reapply that snapshot and delete the file.

The **"snapshot once" rule** is important: `_save_snapshot_once` only writes the
file if it doesn't already exist. A run typically fires many `working`/`needsyou`
events; without this rule the second event would snapshot *our own* blue/amber
lighting and "restore" that on `done`, clobbering the user's real setup. Snapshot
on first takeover only, restore on `done`.

### Webhook retry de-duplication by `event.id`

Anthropic retries a failed delivery with the **same** `event.id`. The server keeps
a bounded `OrderedDict` of seen ids (`already_seen()`, capped at `_SEEN_MAX`); a
repeat returns `204` without re-applying, so a delivery that we already handled
but whose ack got lost doesn't re-flash the board.

### Always return 2xx; never block a hook

A physically-absent keyboard must never turn into an endless webhook retry or a
hung Claude Code hook. So:

- In the server, `KeyboardNotFound` is caught and the request still returns `204`
  — a retry can't reconnect a keyboard that isn't plugged in, so asking Anthropic
  to retry forever is pointless. Any other apply error is logged and also swallowed.
- Unmapped and duplicate events also return `204` (ack and ignore), so subscribing
  to extra event types is harmless.
- On the CLI side, `main()` maps `KeyboardNotFound` to a clean exit; the
  `examples/emberglow-hook` wrapper keeps a missing board from failing the hook.

## Data flow: one webhook event, end to end

A `session.status_idled` event arrives while the server is running with a signing
key configured:

1. `server.webhook()` reads the **raw** body via `request.get_data(as_text=True)`
   — raw bytes are required because the HMAC is computed over them.
2. `verifier.beta.webhooks.unwrap(raw, headers=...)` (the `anthropic` SDK) checks
   the Standard-Webhooks signature and timestamp; on failure it returns `400`.
   On success we read `event.id` and `event.data.type`.
3. `already_seen(event_id)` — if this id was handled before, return `204`.
4. `state_for_event("session.status_idled")` (in `states.py`) → `"needsyou"`.
   An unmapped type returns `204`.
5. `kb.apply("needsyou")` → `Keyboard.apply` validates the name, takes `_lock`,
   and calls `self._open()`.
6. `Keyboard._apply(dev, "needsyou")` → `_save_snapshot_once(dev)` (captures the
   user's lighting if not already saved), then `set_value` for color, brightness,
   speed, and the breathing effect index.
7. Each `set_value` calls `xfer(dev, ...)`, which pads to `REPORT_LEN`, prepends
   the `0x00` report-ID byte, and `dev.write(...)`s the VIA packet to the Q10.
8. `finally: dev.close()`; the handler returns `204`.

## How it's tested

The layering maps one-to-one onto the test suite, and the default run needs no
hardware (`FakeHidDevice`, `RecordingKeyboard`, and `sign_webhook()` live in
[`tests/conftest.py`](../tests/conftest.py)):

- **`tests/test_states.py`** exercises the pure-data layer: `STATES` contents and
  `state_for_event()` / `EVENT_STATE` mappings — no mocks needed.
- **`tests/test_keyboard.py`** drives the `Keyboard` engine against `FakeHidDevice`
  via the `keyboard` fixture, asserting on the decoded `fake_device.set_values`
  and on snapshot/restore behavior (including the "snapshot once" rule) using the
  injected `tmp_path` state file and no-op `sleep`.
- **`tests/test_server.py`** builds an app with `create_app(RecordingKeyboard(),
  signing_key=TEST_SIGNING_KEY)` and posts bodies from `sign_webhook()`, checking
  verification, de-dup, event mapping, and the 2xx-on-missing-keyboard behavior
  through Flask's test client.
- **`tests/test_e2e.py`** runs the real Flask app on an actual socket and posts
  genuinely-signed webhooks over HTTP, covering the whole path end to end.
- **Hardware tests** are opt-in (`pytest -m hardware`) and briefly flash each state
  on a connected board.
