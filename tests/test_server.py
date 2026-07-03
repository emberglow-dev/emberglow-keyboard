"""Unit/integration tests for the Flask webhook app (via the test client)."""

from emberglow import create_app, keyboard as kbmod
from emberglow.states import HUE_BLUE

from conftest import RecordingKeyboard, TEST_SIGNING_KEY, sign_webhook


def _client(kb, **kwargs):
    return create_app(kb, **kwargs).test_client()


def test_health_reports_verifying():
    kb = RecordingKeyboard()
    c = _client(kb, signing_key=TEST_SIGNING_KEY)
    resp = c.get("/")
    assert resp.status_code == 200
    assert resp.json["verifying"] is True
    assert "needsyou" in resp.json["states"]


def test_valid_signed_event_applies_state():
    kb = RecordingKeyboard()
    c = _client(kb, signing_key=TEST_SIGNING_KEY)
    payload, headers = sign_webhook("session.status_run_started")
    resp = c.post("/webhook", data=payload, headers=headers)
    assert resp.status_code == 204
    assert kb.applied == ["working"]


def test_bad_signature_is_rejected_and_ignored():
    kb = RecordingKeyboard()
    c = _client(kb, signing_key=TEST_SIGNING_KEY)
    payload, headers = sign_webhook("session.status_run_started")
    headers["webhook-signature"] = "v1,not-a-real-signature"
    resp = c.post("/webhook", data=payload, headers=headers)
    assert resp.status_code == 400
    assert kb.applied == []


def test_tampered_body_is_rejected():
    kb = RecordingKeyboard()
    c = _client(kb, signing_key=TEST_SIGNING_KEY)
    payload, headers = sign_webhook("session.status_run_started")
    tampered = payload.replace("run_started", "terminated")
    resp = c.post("/webhook", data=tampered, headers=headers)
    assert resp.status_code == 400
    assert kb.applied == []


def test_unmapped_event_is_acked_without_lighting():
    kb = RecordingKeyboard()
    c = _client(kb, signing_key=TEST_SIGNING_KEY)
    payload, headers = sign_webhook("session.status_scheduled")
    resp = c.post("/webhook", data=payload, headers=headers)
    assert resp.status_code == 204
    assert kb.applied == []


def test_duplicate_delivery_applies_once():
    kb = RecordingKeyboard()
    c = _client(kb, signing_key=TEST_SIGNING_KEY)
    payload, headers = sign_webhook("session.status_idled", event_id="event_dup")
    c.post("/webhook", data=payload, headers=headers)
    c.post("/webhook", data=payload, headers=headers)  # retry, same event.id
    assert kb.applied == ["needsyou"]


def test_missing_key_refuses_when_not_unverified():
    kb = RecordingKeyboard()
    c = _client(kb)  # no signing key, allow_unverified defaults to False
    resp = c.post("/webhook", data="{}", headers={"content-type": "application/json"})
    assert resp.status_code == 503
    assert kb.applied == []


def test_unverified_mode_accepts_plain_json():
    kb = RecordingKeyboard()
    c = _client(kb, allow_unverified=True)
    body = '{"id": "local-1", "data": {"type": "session.status_idled"}}'
    resp = c.post("/webhook", data=body, headers={"content-type": "application/json"})
    assert resp.status_code == 204
    assert kb.applied == ["needsyou"]


def test_test_route_gated_behind_unverified():
    kb = RecordingKeyboard()
    assert _client(kb, signing_key=TEST_SIGNING_KEY).post("/test/working").status_code == 403
    ok = _client(kb, allow_unverified=True).post("/test/working")
    assert ok.status_code == 200
    assert kb.applied == ["working"]


def test_keyboard_error_still_acks(monkeypatch):
    # A missing keyboard must not cause Anthropic to retry forever.
    kb = RecordingKeyboard(raises=kbmod.KeyboardNotFound("unplugged"))
    c = _client(kb, signing_key=TEST_SIGNING_KEY)
    payload, headers = sign_webhook("session.status_run_started")
    resp = c.post("/webhook", data=payload, headers=headers)
    assert resp.status_code == 204
