"""Unit tests for the pure state/event-mapping layer."""

from emberglow.states import EVENT_STATE, STATES, state_for_event


def test_every_event_maps_to_a_known_state():
    for event_type, state in EVENT_STATE.items():
        assert state in STATES, f"{event_type} maps to unknown state {state!r}"


def test_state_for_event_known_and_unknown():
    assert state_for_event("session.status_run_started") == "working"
    assert state_for_event("session.status_idled") == "needsyou"
    assert state_for_event("session.status_terminated") == "failed"
    assert state_for_event("session.outcome_evaluation_ended") == "done"
    assert state_for_event("session.status_scheduled") is None
    assert state_for_event("totally.made.up") is None


def test_active_states_breathe_terminal_states_are_solid():
    assert STATES["working"].breathing is True   # pulsing while Claude works
    assert STATES["needsyou"].breathing is True  # pulsing when it needs you
    assert STATES["done"].breathing is False
    assert STATES["failed"].breathing is False


def test_every_state_has_a_description():
    for name, state in STATES.items():
        assert state.description, f"state {name!r} is missing a description"
