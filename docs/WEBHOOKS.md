# Webhooks — lighting the Q10 from Anthropic events 🔥🪝

The webhook path lets **Anthropic (Managed Agents)** drive your keyboard. When a
session (or deployment) changes state, Anthropic POSTs a small, HMAC-signed event
to an HTTPS endpoint you register. Emberglow's Flask server verifies the
signature, maps the event to one of its four lighting states, and drives the
board — the same lighting engine the CLI uses.

Payloads are **thin**: they carry an event type and a few IDs, not session
contents. Emberglow only ever reads `data.type` to pick a state, so nothing
sensitive from your run touches this process.

> New to Emberglow? Start with the [README](../README.md). This doc is the deep
> dive on the `emberglow serve` path specifically.

---

## Setup

1. **Register the endpoint.** In the Anthropic Console go to *Manage → Webhooks*
   and add your public `https://.../webhook` URL.

2. **Subscribe to event types.** Pick the `session.*`, `deployment_run.*`, and
   `vault_credential.*` events you care about (see the table below). Over-
   subscribing is safe — unmapped events are acknowledged and ignored.

3. **Copy the signing secret.** The Console shows a `whsec_...` secret **once**.
   Copy it now; you can't read it again later.

4. **Run the server** with that secret in the environment:

   ```bash
   # Windows (persist): setx ANTHROPIC_WEBHOOK_SIGNING_KEY whsec_xxx
   export ANTHROPIC_WEBHOOK_SIGNING_KEY=whsec_xxx
   emberglow serve                 # listens on 0.0.0.0:8787, POST /webhook
   ```

5. **Expose it publicly** so Anthropic can reach your machine. Any tunnel works:

   ```bash
   cloudflared tunnel --url http://localhost:8787
   # or:  ngrok http 8787
   ```

   Put the tunnel's `https://.../webhook` URL into the Console registration.

`emberglow serve` builds the app via `create_app_from_env()`, which reads
`ANTHROPIC_WEBHOOK_SIGNING_KEY` and `KB_ALLOW_UNVERIFIED` from the environment.

---

## Event → state mapping

The mapping lives in [`emberglow/states.py`](../emberglow/states.py) as the
`EVENT_STATE` dict and is reproduced verbatim here:

| Event (`data.type`) | State | Look |
|---|---|---|
| `session.status_run_started` | `working` | blue, solid |
| `session.status_idled` | `needsyou` | amber, breathing |
| `session.thread_idled` | `needsyou` | amber, breathing |
| `session.status_terminated` | `failed` | red, solid |
| `session.outcome_evaluation_ended` | `done` | green flash → restore |
| `deployment_run.started` | `working` | blue, solid |
| `deployment_run.succeeded` | `done` | green flash → restore |
| `deployment_run.failed` | `failed` | red, solid |
| `vault_credential.refresh_failed` | `failed` | red, solid |

Any event type **not** in this table is acknowledged with `204` and ignored, so
subscribing to extra events in the Console never causes errors or stray lighting.

What the mapped events mean:

- **`session.status_run_started`** — a session began doing work → **working**.
- **`session.status_idled`** — the session is idle and it's your turn (approve a
  permission, answer a question) → **needsyou**.
- **`session.thread_idled`** — a subagent thread is waiting (multi-agent runs) →
  **needsyou**.
- **`session.status_terminated`** — the session ended abnormally → **failed**.
- **`session.outcome_evaluation_ended`** — the run's outcome evaluation finished
  → **done** (green flash, then your prior lighting is restored).
- **`deployment_run.started` / `.succeeded` / `.failed`** — a deployment run's
  lifecycle → **working** / **done** / **failed** respectively.
- **`vault_credential.refresh_failed`** — a stored credential couldn't refresh →
  **failed**.

To change what any event does, edit `EVENT_STATE` only — no other code changes.

---

## Signature verification

Emberglow verifies webhooks with the official **anthropic** SDK (the
`anthropic[webhooks]` extra, already a dependency). When a signing key is set,
`create_app` constructs `anthropic.Anthropic(webhook_key=...)` and each request
is checked with:

```python
event = verifier.beta.webhooks.unwrap(raw, headers=dict(request.headers))
```

This implements the **Standard Webhooks** scheme: an HMAC computed over the
**raw request body** plus the `webhook-id` / `webhook-timestamp` / `webhook-
signature` headers. It also **rejects payloads more than ~5 minutes old** to
block replays.

Key rules:

- **Verify the raw bytes.** The server reads `request.get_data(as_text=True)` and
  passes it straight to `unwrap()`. Never parse and re-serialize the JSON before
  verifying — re-serialization changes the bytes and the HMAC will not match.
- **Bad or absent signature → `400`.** A failed `unwrap()` (bad signature, stale
  timestamp, or malformed body) is logged and returns `400 invalid signature`;
  no lighting is applied.
- **No key configured → `503`.** If `ANTHROPIC_WEBHOOK_SIGNING_KEY` is unset and
  unverified mode is off, the server refuses every request with
  `503 server not configured for verification`.
- **Retries are de-duplicated.** Anthropic retries failed deliveries with the
  **same `event.id`**. The server remembers recently-seen IDs (a bounded
  `OrderedDict`, up to 2048 entries) and returns `204` for a duplicate without
  re-driving the keyboard.
- **A missing keyboard still returns 2xx.** If the Q10 is unplugged
  (`KeyboardNotFound`) or applying a state throws, the error is logged but the
  response is still `204` — a retry can't reconnect physical hardware, so we
  don't ask Anthropic to keep retrying forever.

---

## Endpoints

| Method & path | Purpose |
|---|---|
| `GET /` and `GET /healthz` | Health check. Returns `{ok, verifying, states}` — `verifying` is `true` when a signing key is loaded, and `states` lists each state name and description. |
| `POST /webhook` | The receiver. Verifies, de-dups by `event.id`, maps `data.type` → state, drives the board. Returns `204` on success/ignored, `400` on bad signature, `503` when unconfigured. |
| `POST /test/<state>` | Dev-only. Applies a named state (`working`/`needsyou`/`done`/`failed`) by hand. Returns `403` unless `KB_ALLOW_UNVERIFIED=1`; `400` for an unknown state. |

---

## Local testing

You don't need a real Anthropic account or a public tunnel to try the server.

**By hand, with unverified mode:**

```bash
KB_ALLOW_UNVERIFIED=1 emberglow serve
curl -X POST http://localhost:8787/test/needsyou     # keyboard breathes amber
```

`KB_ALLOW_UNVERIFIED=1` skips signature checks and enables the `/test/<state>`
route. In this mode `POST /webhook` also accepts plain, unsigned JSON like
`{"id": "local-1", "data": {"type": "session.status_idled"}}`.

**Automated tests** need neither hardware nor Anthropic. They use a fake keyboard
(`RecordingKeyboard` / `FakeHidDevice`) and a **locally-signed** payload produced
by `sign_webhook()` in [`tests/conftest.py`](../tests/conftest.py) — which signs
with a test `whsec_` key via the `standardwebhooks` library, exactly the scheme
`unwrap()` verifies. So the full verify → map → drive path is exercised offline:

```bash
pytest                 # unit + e2e over a real socket, no hardware, no account
pytest -m hardware     # opt-in: flashes each state on a connected Q10
```

See [`tests/test_server.py`](../tests/test_server.py) (test client) and
[`tests/test_e2e.py`](../tests/test_e2e.py) (real HTTP server on a socket).

---

## Security

- **Never commit the `whsec_` signing key.** Keep it in the environment (or a
  secrets manager), never in the repo or a config file you check in.
- **Never enable `KB_ALLOW_UNVERIFIED` on a public endpoint.** It disables
  signature verification and opens the `/test/<state>` route — fine for
  localhost, dangerous anywhere reachable from the internet.

---

## Reference

- Anthropic webhook docs: <https://platform.claude.com/docs/en/managed-agents/webhooks>
- Event → state mapping: [`emberglow/states.py`](../emberglow/states.py)
- Server implementation: [`emberglow/server.py`](../emberglow/server.py)
