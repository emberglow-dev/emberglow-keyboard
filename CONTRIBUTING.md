# Contributing to Emberglow 🔥⌨️

Thanks for wanting to make Emberglow better! It's a small, focused project —
light a **Keychron Q10** from Claude activity over the **VIA protocol (QMK raw
HID)** — and it's built so you can hack on almost all of it *without* a keyboard
plugged in. This guide gets you set up and shows the concrete steps for the most
common contributions.

New here? Skim [`README.md`](README.md) for what the project does and
[`CLAUDE.md`](CLAUDE.md) for how the code is laid out.

---

## Development setup

```bash
git clone https://github.com/emberglow-dev/emberglow-keyboard
cd emberglow-keyboard
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -e ".[dev]"
```

That editable install pulls in the runtime deps (`hidapi`, `flask`,
`anthropic[webhooks]`) plus `pytest` from the `dev` extra. Python **3.10+** is
required.

You do **not** need a Keychron Q10 to develop or run the test suite — see below.

---

## Running tests

```bash
pytest                 # 24 tests, no hardware needed
pytest -m hardware     # opt-in: flashes each state on a real connected board
```

`pytest` is configured (in `pyproject.toml`) to deselect the `hardware` marker
by default, so the standard run never touches a physical keyboard. It exercises
the pure state layer, the `Keyboard` driver against a fake HID device, the Flask
app via a test client, and a real HTTP server over a socket.

The `-m hardware` tests are opt-in: they open the real Q10 and briefly flash
each lighting state. They `pytest.skip` gracefully if no board is connected.

### The test fixtures (in `tests/conftest.py`)

- **`FakeHidDevice`** — records every VIA write and returns zeroed replies for
  reads. Its `set_values` property decodes writes into convenient
  `(value_id, data)` tuples, and `last_color()` / `last_effect()` help you
  assert on what got sent. Prefer asserting on `set_values` over raw byte lists.
- **`keyboard`** fixture — a real `Keyboard` wired to a `FakeHidDevice`, an
  isolated `state_file` under `tmp_path`, and a no-op `sleep` (so the "done"
  flash hold doesn't slow tests).
- **`RecordingKeyboard`** — a stand-in for the server's keyboard that just
  records the state names `apply()` was called with (and can be told to raise).
- **`sign_webhook(event_type, ...)`** — produces a genuinely HMAC-signed webhook
  `(payload, headers)` pair using a known test signing key, so server tests
  cover the real verify → map → drive path.

Because of these, **no physical keyboard is required** for the default run.

---

## Project layout

```
emberglow/
  states.py     Pure data: LightingState, STATES, EVENT_STATE, state_for_event(). No I/O.
  keyboard.py   The ONLY module that touches hardware. VIA framing + Keyboard class.
  server.py     create_app(...) Flask webhook factory.
  cli.py        argparse entry point (`emberglow`): set/restore/status/probe/enumerate/serve.
tests/          Unit (fake HID), server (test client), e2e (real socket). conftest.py fixtures.
examples/       Claude Code hook settings + a venv wrapper script.
docs/           Architecture, VIA protocol, and reference docs.
```

For depth, see [`CLAUDE.md`](CLAUDE.md) and
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## How to make common contributions

### (a) Add a new lighting state

1. Add an entry to `STATES` in `emberglow/states.py` — a `LightingState` with a
   `hue` (0–255 VIA scale), `breathing` flag, and a `description`.
2. If an Anthropic webhook event should trigger it, add a `data.type` → state
   mapping to `EVENT_STATE` in the same file.
3. Add a case to `tests/test_states.py` (e.g. that the state exists, has a
   description, and breathes or not as intended).

No hardware code changes are needed unless the state needs special behavior like
`done`, which is flash-then-restore logic living in `Keyboard._apply`.

### (b) Change what an Anthropic webhook event does

Edit `EVENT_STATE` in `emberglow/states.py` — **only** that dict. Unmapped
events are acknowledged (2xx) and ignored, so it's safe to add or remove
mappings. Update the table in `tests/test_states.py` if you change a mapping.

### (c) Touch the VIA protocol / add a command

All hardware access lives in `emberglow/keyboard.py` — keep it that way. When
adding or changing VIA framing, keep the low-level functions taking an
**injectable `dev`** (e.g. `set_value(dev, ...)`, `get_value(dev, ...)`) so
tests can drive them with `FakeHidDevice`. The `Keyboard` class similarly takes
injectable `open_device`, `state_file`, and `sleep` — preserve that so tests
stay hardware-free.

### (d) Support a different keyboard

Emberglow is built and tested against the Q10, but the VIA/QMK approach works for
most VIA-enabled QMK boards. Another board needs its own USB VID/PID and effect
indices. See [`docs/PROTOCOL.md`](docs/PROTOCOL.md) for what to change and how to
discover the values (`emberglow enumerate` and `emberglow probe` help).

---

## Code style

There's no enforced linter or formatter configured, so please **match the
surrounding style**. Concretely, the codebase uses:

- **Python 3.10+**.
- `from __future__ import annotations` at the top of modules.
- **Type hints** on function signatures (including `Protocol` for the HID
  interface).
- **Docstrings** on modules, classes, and non-trivial functions.
- **Dependency injection** for testability — pass `open_device`, `state_file`,
  `sleep`, `keyboard`, `signing_key`, etc. rather than hard-wiring them.
- **Hardware access confined to `keyboard.py`** — `states.py` stays pure data,
  and everything else drives the board only through the `Keyboard` class.

---

## Pull request checklist

- [ ] Tests pass: `pytest`.
- [ ] New behavior has a test (assert on `fake_device.set_values` /
      `RecordingKeyboard.applied` where you can).
- [ ] User-facing changes are reflected in the docs (`README.md`, `CLAUDE.md`,
      or `docs/`).
- [ ] No secrets committed — **never** commit a `whsec_...` webhook signing key
      or a `.env` file. Keep secrets in the environment
      (`ANTHROPIC_WEBHOOK_SIGNING_KEY`).

---

## Reporting bugs / requesting features

Please open an issue on GitHub:
<https://github.com/emberglow-dev/emberglow-keyboard/issues>. A note on your OS,
Python version, keyboard/firmware, and the exact `emberglow` command or webhook
event helps a lot.
