"""Deterministic lifecycle rules for an interactive simulation session."""
from __future__ import annotations

from dataclasses import dataclass

from .models import SessionState, utc_now


class InvalidSessionTransition(ValueError):
    reason_code = "INVALID_SESSION_TRANSITION"


_TRANSITIONS: dict[SessionState, frozenset[SessionState]] = {
    SessionState.CREATED: frozenset({SessionState.PREPARING, SessionState.ABORTED, SessionState.INTERRUPTED}),
    SessionState.PREPARING: frozenset({SessionState.READY, SessionState.FAILED, SessionState.ABORTED, SessionState.INTERRUPTED}),
    SessionState.READY: frozenset({SessionState.RUNNING, SessionState.STOPPING, SessionState.FAILED, SessionState.ABORTED, SessionState.INTERRUPTED}),
    SessionState.RUNNING: frozenset({SessionState.PAUSED, SessionState.STOPPING, SessionState.FAILED, SessionState.ABORTED, SessionState.INTERRUPTED}),
    SessionState.PAUSED: frozenset({SessionState.RUNNING, SessionState.STOPPING, SessionState.FAILED, SessionState.ABORTED, SessionState.INTERRUPTED}),
    SessionState.STOPPING: frozenset({SessionState.COMPLETED, SessionState.FAILED, SessionState.ABORTED}),
    SessionState.COMPLETED: frozenset(),
    SessionState.FAILED: frozenset(),
    SessionState.ABORTED: frozenset(),
    SessionState.INTERRUPTED: frozenset(),
}


@dataclass(frozen=True)
class StateTransition:
    previous: SessionState
    current: SessionState
    revision: int
    reason_code: str
    wall_time_iso: str


class SessionStateMachine:
    def __init__(self, initial_state: SessionState = SessionState.CREATED, initial_revision: int = 0) -> None:
        if initial_revision < 0:
            raise ValueError("initial_revision must be non-negative")
        self._state = initial_state
        self._revision = initial_revision

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def revision(self) -> int:
        return self._revision

    def transition(self, target: SessionState, reason_code: str = "OK") -> StateTransition:
        if target not in _TRANSITIONS[self._state]:
            raise InvalidSessionTransition(f"cannot transition from {self._state} to {target}")
        previous = self._state
        self._state = target
        self._revision += 1
        return StateTransition(previous, target, self._revision, reason_code, utc_now().isoformat())


def allowed_transitions(state: SessionState) -> frozenset[SessionState]:
    return _TRANSITIONS[state]
