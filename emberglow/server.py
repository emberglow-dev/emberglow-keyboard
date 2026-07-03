"""Flask webhook receiver that lights the keyboard from Anthropic events.

Anthropic (Managed Agents) POSTs thin, HMAC-signed events when a session
changes state. We verify the Standard-Webhooks signature with the anthropic
SDK, map ``data.type`` → a lighting state, and drive the :class:`Keyboard`.

Use :func:`create_app` for tests (inject a fake keyboard + a known key) and
:func:`create_app_from_env` for the ``emberglow serve`` entry point.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import OrderedDict
from typing import Optional

from flask import Flask, request

from .keyboard import Keyboard, KeyboardNotFound
from .states import STATES, state_for_event

log = logging.getLogger("emberglow")

_SEEN_MAX = 2048


def create_app(
    keyboard: Optional[Keyboard] = None,
    *,
    signing_key: Optional[str] = None,
    allow_unverified: bool = False,
) -> Flask:
    """Build the webhook app.

    Args:
        keyboard: the :class:`Keyboard` to drive (defaults to a real one).
        signing_key: ``whsec_...`` secret; when set, every request's signature
            is verified. When None, requests are only accepted if
            ``allow_unverified`` is True (local testing).
        allow_unverified: trust request bodies without a signature. NEVER enable
            on a public endpoint — only for localhost development.
    """
    app = Flask(__name__)
    kb = keyboard or Keyboard()

    verifier = None
    if signing_key:
        import anthropic

        verifier = anthropic.Anthropic(webhook_key=signing_key)

    seen: "OrderedDict[str, None]" = OrderedDict()

    def already_seen(event_id: str) -> bool:
        # Anthropic retries failed deliveries with the SAME event.id.
        if event_id in seen:
            return True
        seen[event_id] = None
        while len(seen) > _SEEN_MAX:
            seen.popitem(last=False)
        return False

    @app.get("/")
    @app.get("/healthz")
    def health():
        return {
            "ok": True,
            "verifying": verifier is not None,
            "states": {name: s.description for name, s in STATES.items()},
        }

    @app.post("/webhook")
    def webhook():
        raw = request.get_data(as_text=True)  # MUST be the raw body for the HMAC

        if verifier is not None:
            try:
                event = verifier.beta.webhooks.unwrap(
                    raw, headers=dict(request.headers)
                )
            except Exception as e:  # bad signature, stale timestamp, malformed
                log.warning("webhook verification failed: %s", e)
                return "invalid signature", 400
            event_id, event_type = event.id, event.data.type
        elif allow_unverified:
            body = json.loads(raw or "{}")
            event_id = body.get("id", f"local-{time.time()}")
            event_type = body.get("data", {}).get("type", "")
        else:
            log.error("no signing key configured; refusing unverified request")
            return "server not configured for verification", 503

        if already_seen(event_id):
            return "", 204  # duplicate retry — already handled

        state = state_for_event(event_type)
        if state is None:
            return "", 204  # subscribed-but-uninteresting event; ack and ignore

        try:
            kb.apply(state)
            log.info("%s -> %s", event_type, state)
        except KeyboardNotFound as e:
            # 200 anyway: a retry won't reconnect a physically-absent keyboard.
            log.error("keyboard unavailable: %s", e)
        except Exception:
            log.exception("failed to apply state %s", state)

        return "", 204

    @app.post("/test/<state>")
    def test_state(state):
        """Fire a lighting state by hand (dev only)."""
        if not allow_unverified:
            return "set KB_ALLOW_UNVERIFIED=1 to enable test routes", 403
        if state not in STATES:
            return {"error": "unknown state", "known": sorted(STATES)}, 400
        kb.apply(state)
        return {"applied": state}

    return app


def create_app_from_env() -> Flask:
    """Build the app from environment variables (used by ``emberglow serve``)."""
    return create_app(
        signing_key=os.environ.get("ANTHROPIC_WEBHOOK_SIGNING_KEY"),
        allow_unverified=os.environ.get("KB_ALLOW_UNVERIFIED") == "1",
    )
