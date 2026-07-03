"""End-to-end tests.

The default e2e test runs a real HTTP server on a socket and posts a genuinely
signed webhook to it — exercising the full verify → map → drive stack over the
wire, with a fake keyboard backend (no hardware).

The hardware e2e test is marked ``@pytest.mark.hardware`` and is skipped unless
you run ``pytest -m hardware`` with a Q10 connected — it briefly flashes each
lighting state on the real board.
"""

from __future__ import annotations

import threading
import urllib.request

import pytest
from werkzeug.serving import make_server

from emberglow import create_app
from emberglow.states import STATES

from conftest import RecordingKeyboard, TEST_SIGNING_KEY, sign_webhook


class _BackgroundServer:
    def __init__(self, app):
        self._srv = make_server("127.0.0.1", 0, app)  # port 0 -> OS picks a free one
        self.port = self._srv.server_port
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._srv.shutdown()
        self._thread.join(timeout=5)


def _post(port, path, data, headers):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data.encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_e2e_signed_webhook_over_http():
    kb = RecordingKeyboard()
    app = create_app(kb, signing_key=TEST_SIGNING_KEY)
    with _BackgroundServer(app) as srv:
        payload, headers = sign_webhook("session.status_idled", event_id="e2e-1")
        status = _post(srv.port, "/webhook", payload, headers)
    assert status == 204
    assert kb.applied == ["needsyou"]


def test_e2e_bad_signature_over_http():
    kb = RecordingKeyboard()
    app = create_app(kb, signing_key=TEST_SIGNING_KEY)
    with _BackgroundServer(app) as srv:
        payload, headers = sign_webhook("session.status_idled", event_id="e2e-2")
        headers["webhook-signature"] = "v1,bogus"
        status = _post(srv.port, "/webhook", payload, headers)
    assert status == 400
    assert kb.applied == []


@pytest.mark.hardware
@pytest.mark.parametrize("state", sorted(STATES))
def test_e2e_real_keyboard_flashes_each_state(state):
    """Requires a connected Q10: pytest -m hardware. Flashes each state briefly."""
    import time

    from emberglow.keyboard import Keyboard, KeyboardNotFound

    kb = Keyboard()
    try:
        kb.apply(state)
    except KeyboardNotFound as e:
        pytest.skip(f"no keyboard connected: {e}")
    time.sleep(0.8)
