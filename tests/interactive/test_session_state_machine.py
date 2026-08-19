from __future__ import annotations

import pytest

from sat_sim.interactive.models import SessionState
from sat_sim.interactive.state_machine import InvalidSessionTransition, SessionStateMachine, allowed_transitions


def test_nominal_session_lifecycle_has_monotonic_revisions() -> None:
    machine = SessionStateMachine()
    events = [
        machine.transition(SessionState.PREPARING),
        machine.transition(SessionState.READY),
        machine.transition(SessionState.RUNNING),
        machine.transition(SessionState.PAUSED),
        machine.transition(SessionState.RUNNING),
        machine.transition(SessionState.STOPPING),
        machine.transition(SessionState.COMPLETED),
    ]
    assert [event.revision for event in events] == list(range(1, 8))
    assert machine.state is SessionState.COMPLETED


def test_illegal_transition_and_terminal_transition_fail_closed() -> None:
    machine = SessionStateMachine()
    with pytest.raises(InvalidSessionTransition, match="CREATED.*RUNNING"):
        machine.transition(SessionState.RUNNING)
    machine.transition(SessionState.ABORTED)
    with pytest.raises(InvalidSessionTransition):
        machine.transition(SessionState.PREPARING)


@pytest.mark.parametrize("source", list(SessionState))
@pytest.mark.parametrize("target", list(SessionState))
def test_every_state_pair_matches_the_frozen_transition_matrix(source: SessionState, target: SessionState) -> None:
    machine = SessionStateMachine(source, initial_revision=7)
    if target in allowed_transitions(source):
        event = machine.transition(target, "MATRIX_TEST")
        assert event.previous is source
        assert event.current is target
        assert event.revision == 8
    else:
        with pytest.raises(InvalidSessionTransition):
            machine.transition(target)
