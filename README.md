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
software, no OpenRGB server — and plugs into Claude two ways:

- **Claude Code hooks** → the CLI: `emberglow set needsyou`
- **Anthropic (Managed Agents) webhooks** → the server: `emberglow serve`

---

## Requirements

- A Keychron Q10 on **wired** USB (raw HID isn't exposed over Bluetooth/2.4GHz).
- Python **3.10+**.
- The **VIA app/tab closed** while Emberglow runs — only one process can hold the
  raw-HID interface at a time.

> Emberglow is built and tested against the Q10, but the VIA/QMK approach works
> for most VIA-enabled QMK boards. Other boards need their USB VID/PID and effect
> indices — see [`docs/PROTOCOL.md`](docs/PROTOCOL.md).

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
