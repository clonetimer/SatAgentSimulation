"""Recorder protocol regressions with a controlled clock; not native/24h evidence."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from sat_sim.interactive import continuous_endurance as endurance
from sat_sim.interactive.models import SessionState


def _record_until_stopping(tmp_path, monkeypatch):
    session = SimpleNamespace(
        spec=SimpleNamespace(continuous=True, session_id="terminal-snapshot", quantum_s=.5),
        _workspace=SimpleNamespace(path=tmp_path), state=SessionState.RUNNING,
    )
    clock = {"wall": 0.}

    def observe(_session):
        wall = clock["wall"]
        sim = min(wall, 86400.)
        return {
            "schema_version": "sat-continuous-observability.v1",
            "session_id": session.spec.session_id,
            "sampled_at": (datetime(2026, 1, 1, tzinfo=timezone.utc)
                           + timedelta(seconds=wall)).isoformat(),
            "session_state": session.state.value,
            "wall_elapsed_s": wall, "sim_elapsed_s": sim,
            "telemetry": {"status": "FRESH", "lag_wall_s": 0.,
                          "last_sequence": int(sim * 2), "last_sim_time_s": sim,
                          "durable_frame_count": int(sim * 2), "durable_bytes": 100},
            "disk": {"available": True, "free_bytes": 2**40, "reserved_bytes": 1024},
            "process": {"available": True, "resident_bytes": 1024},
            "resume_supported": False, "automatic_tc_replay_allowed": False,
        }

    monkeypatch.setattr(endurance, "observe_continuous_session", observe)
    recorder = endurance.EnduranceRecorder(session, interval_s=3600.)
    recorder.path.touch()
    recorder._status = "RECORDING"
    for hour in range(25):
        clock["wall"] = float(hour * 3600)
        recorder._sample()
    session.state = SessionState.STOPPING
    clock["wall"] = 86401.
    recorder._sample()
    return recorder, session, clock


@pytest.mark.parametrize("terminal", [
    SessionState.COMPLETED, SessionState.FAILED,
    SessionState.ABORTED, SessionState.INTERRUPTED,
])
def test_stop_records_actual_terminal_transition_between_periodic_samples(
    tmp_path, monkeypatch, terminal,
):
    recorder, session, clock = _record_until_stopping(tmp_path, monkeypatch)
    session.state = terminal
    clock["wall"] = 86402.
    recorder._stop.set()
    recorder._run()
    status = recorder.status()
    assert status["last_observation"]["session_state"] == terminal.value
    assert status["summary"]["eligible_for_manual_24h_review"] is (
        terminal == SessionState.COMPLETED
    )
    assert status["summary"]["qualified_24h_wallclock"] is False
    assert status["summary"]["observed_running_wall_s"] == 86400.
    assert endurance.verify_endurance_summary(recorder.summary_path)["evidence"]["samples"] == 27


def test_stop_does_not_invent_completion_while_still_stopping(tmp_path, monkeypatch):
    recorder, _, _ = _record_until_stopping(tmp_path, monkeypatch)
    recorder._stop.set()
    recorder._run()
    assert recorder.status()["last_observation"]["session_state"] == "STOPPING"
    assert recorder.status()["summary"]["eligible_for_manual_24h_review"] is False


def test_failed_terminal_observation_is_incomplete_not_a_qualified_summary(tmp_path, monkeypatch):
    recorder, session, _ = _record_until_stopping(tmp_path, monkeypatch)
    session.state = SessionState.COMPLETED

    def unavailable(_session):
        raise OSError("terminal observation unavailable")

    monkeypatch.setattr(endurance, "observe_continuous_session", unavailable)
    recorder._stop.set()
    recorder._run()
    assert recorder.status()["recording_status"] == "INCOMPLETE_ERROR"
    assert recorder.status()["summary"]["eligible_for_manual_24h_review"] is False
