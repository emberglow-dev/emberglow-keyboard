"""Lighting states and the Claude-webhook-event → state mapping.

This module is pure data (no hardware, no I/O) so it can be unit-tested and
reasoned about on its own. Everything a contributor needs to add or retune a
state or an event mapping lives here.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---- VIA hue scale (0-255) ---------------------------------------------------
# The Q10 firmware takes hue as a single 0-255 byte (not 0-360 degrees).
HUE_RED = 0
HUE_ORANGE = 12
HUE_AMBER = 21
HUE_GREEN = 85
HUE_BLUE = 170


@dataclass(frozen=True)
class LightingState:
    """A named lighting look.

    Attributes:
        hue:        0-255 VIA hue.
        breathing:  True → breathing effect, False → solid.
        description: Human-readable meaning (shown in ``emberglow`` help/health).
    """

    hue: int
    breathing: bool
    description: str = ""


# The four looks Emberglow can put on the board. "done" is special-cased by the
# Keyboard driver: it flashes green, then restores whatever you had before.
STATES: dict[str, LightingState] = {
    "working":  LightingState(HUE_BLUE,  True,  "Claude is working"),
    "needsyou": LightingState(HUE_ORANGE, True,  "Claude needs your input"),
    "done":     LightingState(HUE_GREEN, False, "Task finished (then restore)"),
    "failed":   LightingState(HUE_RED,   False, "Something failed"),
}

# Anthropic webhook ``data.type`` → state name. Anything not listed here is
# acknowledged (2xx) and ignored, so subscribing to extra event types is safe.
# See https://platform.claude.com/docs/en/managed-agents/webhooks for the list.
EVENT_STATE: dict[str, str] = {
    "session.status_run_started":       "working",
    "session.status_idled":             "needsyou",   # your turn — approve/answer
    "session.thread_idled":             "needsyou",    # subagent waiting (multiagent)
    "session.status_terminated":        "failed",
    "session.outcome_evaluation_ended": "done",
    "deployment_run.started":           "working",
    "deployment_run.succeeded":         "done",
    "deployment_run.failed":            "failed",
    "vault_credential.refresh_failed":  "failed",
}


def state_for_event(event_type: str) -> str | None:
    """Return the state name for a webhook ``data.type``, or None if unmapped."""
    return EVENT_STATE.get(event_type)
