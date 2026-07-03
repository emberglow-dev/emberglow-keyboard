# Emberglow 🔥⌨️

Light your **Keychron Q10** based on what Claude is doing. Emberglow turns your
keyboard's RGB into an ambient status light:

| State | Look | Fires when |
|-------|------|------------|
| `working` | blue, solid | Claude starts working |
| `needsyou` | amber, breathing | Claude needs your input (a question or approval) |
| `done` | green flash → restore | Claude finishes |
| `failed` | red, solid | something errored / a run failed |

It talks to the keyboard over the **VIA protocol (QMK raw HID)** — no extra
software, no OpenRGB server. It was built for **Claude Code**, but the CLI
(`emberglow set <state>`) is tool-agnostic, so any agent that can run a command
on lifecycle events can drive it:

- **Claude Code hooks** → the CLI: `emberglow set needsyou`
- **Anthropic (Managed Agents) webhooks** → the server: `emberglow serve`
- **OpenAI Codex CLI** and **Google Antigravity** → their notify/hook systems
  (see [below](#use-it-with-openai-codex-cli))

![Emberglow lighting a Keychron Q10 in response to Claude activity](examples/emberglow-demo.gif)

---

## Requirements

- A Keychron Q10 on **wired** USB (raw HID isn't exposed over Bluetooth/2.4GHz).
- Python **3.10+**.
- The **VIA app/tab closed** while Emberglow runs — only one process can hold the
  raw-HID interface at a time.

---

## Supported keyboards

| Keyboard | Status | VID:PID | VIA dialect |
|----------|--------|---------|-------------|
| **Keychron Q10** | ✅ Tested on real hardware | `3434:01A1` | v2 |

Emberglow is built and tested against the **Keychron Q10**, but nothing in the
lighting engine is Q10-specific — the **VIA protocol (QMK raw HID)** it speaks is
shared across VIA-enabled QMK boards. Any such board should work once you point
Emberglow at it:

1. `emberglow enumerate` — find the board's USB **VID/PID** and its raw-HID
   interface (`usage_page=0xFF60`).
2. `emberglow probe` — discover the firmware-specific **effect indices**
   (breathing / solid) by flashing each one.
3. Plug those values in — see [`docs/PROTOCOL.md`](docs/PROTOCOL.md) for exactly
   what to change.

Got another board working? A PR adding it to this table is welcome.

---

## Install

```bash
git clone https://github.com/emberglow-dev/emberglow-keyboard
cd emberglow-keyboard
python -m venv .venv
# Windows:  .venv\Scripts\activate      macOS/Linux:  source .venv/bin/activate
pip install -e .
```

Verify the keyboard is reachable:

```bash
emberglow enumerate     # should list a "QMK raw-HID interface" line for the Q10
emberglow status        # prints the VIA protocol version + current lighting
```

### One-time setup: find your effect indices

Effect indices are firmware-specific (they match your VIA lighting dropdown
order). Discover which index is the breathing effect:

```bash
emberglow probe         # cycles effects; note which index breathes
```

Then set the two indices Emberglow uses (defaults: solid=1, breathing=2):

```bash
# Windows (persist):  setx KB_BREATHING_EFFECT 2   &&  setx KB_SOLID_EFFECT 1
export KB_BREATHING_EFFECT=2
export KB_SOLID_EFFECT=1
```

---

## Use it with Claude Code hooks (local, no server)

This is the simplest path — Claude Code runs a shell command on lifecycle events.
Add this to `~/.claude/settings.json` (all projects) or `.claude/settings.json`
(this project). Full copy in [`examples/claude-code-settings.json`](examples/claude-code-settings.json):

```json
{
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [{ "type": "command", "command": "emberglow set working" }] }
    ],
    "Notification": [
      { "hooks": [{ "type": "command", "command": "emberglow set needsyou" }] }
    ],
    "Stop": [
      { "hooks": [{ "type": "command", "command": "emberglow set done" }] }
    ]
  }
}
```

What each hook maps to:

- **`UserPromptSubmit`** → `working` (blue): you sent a prompt, Claude is on it.
- **`Notification`** → `needsyou` (amber breathing): Claude is waiting on you
  (a permission prompt, or it's been idle waiting for input).
- **`Stop`** → `done` (green flash, then your normal lighting comes back).

**If `emberglow` isn't on your global PATH** (e.g. it's in a venv), point the hook
at the wrapper in [`examples/emberglow-hook`](examples/emberglow-hook):

```json
{ "hooks": [{ "type": "command", "command": "/abs/path/to/examples/emberglow-hook needsyou" }] }
```

> **Windows gotcha:** Claude Code runs hook commands through Git Bash, where `\`
> is an escape character. Use **forward slashes** in any absolute path, or the
> path silently collapses (`C:\Users\...` → `C:Users...` → "command not found"):
>
> ```json
> { "hooks": [{ "type": "command", "command": "C:/Users/you/emberglow-keyboard/.venv/Scripts/emberglow.exe set needsyou" }] }
> ```

Test a hook by hand — it's just the CLI:

```bash
emberglow set needsyou     # keyboard breathes amber
emberglow set done         # green flash, then restores your lighting
```

---

## Use it with OpenAI Codex CLI

Codex CLI can run an external program on lifecycle events via the `notify`
option in `~/.codex/config.toml`, passing the event as a JSON string in the
program's final argument. Today Codex emits **only** the `agent-turn-complete`
event — it fires when Codex finishes a turn and hands control back to you — so
the natural mapping is turn-complete → `needsyou`.

`~/.codex/config.toml`:

```toml
notify = ["python3", "/abs/path/to/emberglow-notify.py"]
```

`emberglow-notify.py`:

```python
#!/usr/bin/env python3
"""Codex `notify` bridge → Emberglow. Codex passes the event as JSON in argv[1]."""
import json, subprocess, sys

if len(sys.argv) >= 2:
    try:
        event = json.loads(sys.argv[1])
    except json.JSONDecodeError:
        event = {}
    if event.get("type") == "agent-turn-complete":
        # Codex finished the turn and is waiting on you.
        subprocess.run(["emberglow", "set", "needsyou"], check=False)
```

> **Limitation:** Codex's `notify` currently exposes only `agent-turn-complete`,
> so there's no native "started working" signal. To also light `working`, wrap
> the `codex` command in a shell alias/function that runs `emberglow set working`
> before launching Codex.

Payload fields are documented in the Codex
[advanced configuration docs](https://developers.openai.com/codex/config-advanced).

---

## Use it with Google Antigravity

> ⚠️ **Verify against the current docs before relying on this.** Antigravity's
> hooks are documented on a JavaScript-rendered site that couldn't be read
> verbatim; the config below is corroborated from Google Cloud Community and
> third-party write-ups, **not** confirmed against the primary docs. Treat the
> exact event names and file paths as *probably right, worth double-checking*.

Antigravity supports **hooks** — shell commands run on agent lifecycle events —
defined in a `hooks.json` file, either globally
(`~/.gemini/antigravity-cli/hooks.json`) or per workspace
(`<project>/.agents/hooks.json`). Reported events include `PreInvocation`,
`PostInvocation`, `PreToolUse`, `PostToolUse`, and `Stop`.

```json
{
  "emberglow-working": {
    "PreInvocation": [
      { "hooks": [ { "type": "command", "command": "emberglow set working", "timeout": 10 } ] }
    ]
  },
  "emberglow-done": {
    "Stop": [
      { "hooks": [ { "type": "command", "command": "emberglow set done", "timeout": 10 } ] }
    ]
  }
}
```

Two Antigravity-specific caveats:

- Unlike Claude Code / Codex fire-and-forget hooks, Antigravity hooks are
  **decision gates**: the script reads event JSON on **stdin** and is expected to
  emit an allow/deny JSON verdict on **stdout** and exit `0`. For a pure side
  effect like `emberglow set`, run the command, then print an "allow" response.
- No lifecycle event corresponding to **"waiting for your approval/input"** is
  confirmed, so `needsyou` may not be expressible here yet.

---

## Use it with Anthropic webhooks (Managed Agents)

Anthropic POSTs signed events when a session changes state. Emberglow verifies
the signature and lights the board.

1. **Register the endpoint** in the Anthropic Console → *Manage → Webhooks*.
   Subscribe to `session.status_*`, `deployment_run.*`, etc. Copy the
   `whsec_...` signing secret it shows once.

2. **Run the server** with that secret in the environment:

   ```bash
   # Windows (persist): setx ANTHROPIC_WEBHOOK_SIGNING_KEY whsec_xxx
   export ANTHROPIC_WEBHOOK_SIGNING_KEY=whsec_xxx
   emberglow serve                 # listens on 0.0.0.0:8787, POST /webhook
   ```

3. **Expose it publicly** so Anthropic can reach it (any tunnel works):

   ```bash
   cloudflared tunnel --url http://localhost:8787
   # or:  ngrok http 8787
   ```

   Put the resulting `https://.../webhook` URL in the Console.

The event → state mapping lives in [`emberglow/states.py`](emberglow/states.py)
(`EVENT_STATE`). Unmapped events are acknowledged and ignored, so subscribing to
extra event types is harmless.

### Local testing without a real webhook

```bash
KB_ALLOW_UNVERIFIED=1 emberglow serve
curl -X POST http://localhost:8787/test/needsyou     # fire a state by hand
```

`KB_ALLOW_UNVERIFIED=1` disables signature checks and enables the `/test/<state>`
route. **Never enable it on a public endpoint.**

---

## CLI reference

```
emberglow set <working|needsyou|done|failed>   apply a lighting state
emberglow restore                              restore pre-takeover lighting
emberglow status                               VIA protocol version + lighting
emberglow probe [--count N] [--hold S]         cycle effects to find breathing
emberglow enumerate                            list Keychron HID interfaces
emberglow serve [--host H] [--port P]          run the webhook server
```

Environment variables: `KB_BREATHING_EFFECT`, `KB_SOLID_EFFECT`,
`ANTHROPIC_WEBHOOK_SIGNING_KEY`, `KB_ALLOW_UNVERIFIED`, `PORT`.

---

## Development

```bash
pip install -e ".[dev]"
pytest                  # unit + e2e (no hardware needed)
pytest -m hardware      # also flash each state on a connected board
```

- **Unit tests** use a fake HID device — no keyboard required.
- **E2E tests** run the real Flask server on a socket and post genuinely-signed
  webhooks over HTTP.
- **Hardware tests** are opt-in (`-m hardware`) and briefly flash the real board.

Architecture, the VIA protocol, and how to contribute are documented in
[`CLAUDE.md`](CLAUDE.md), [`CONTRIBUTING.md`](CONTRIBUTING.md), and
[`docs/`](docs/).

## License

MIT — see [`LICENSE`](LICENSE).
