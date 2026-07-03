"""Emberglow — light your Keychron Q10 from Claude events.

Two integration paths, one lighting engine:

* **Claude Code hooks** call the CLI: ``emberglow set needsyou`` / ``emberglow set done``.
* **Anthropic (Managed Agents) webhooks** POST to the server: ``emberglow serve``.

Public API:
    from emberglow import Keyboard, create_app, STATES, EVENT_STATE
"""

from .states import EVENT_STATE, STATES, LightingState, state_for_event
from .keyboard import Keyboard, KeyboardNotFound, open_device

__version__ = "0.1.0"

__all__ = [
    "Keyboard",
    "KeyboardNotFound",
    "open_device",
    "create_app",
    "STATES",
    "EVENT_STATE",
    "LightingState",
    "state_for_event",
    "__version__",
]


def create_app(*args, **kwargs):
    """Lazy re-export of :func:`emberglow.server.create_app`.

    Imported lazily so ``import emberglow`` doesn't require Flask/anthropic
    (e.g. when only the CLI ``set`` command is used from a hook).
    """
    from .server import create_app as _create_app

    return _create_app(*args, **kwargs)
