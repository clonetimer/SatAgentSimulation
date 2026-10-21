"""R7b: production PersistentSimulationRuntime protocol over owned spawn child.

The contract test never substitutes a Python physics model for native
satellite data: the end-to-end tests require actual Basilisk/bsk and compare
the real unified spacecraft graph on each side.
"""
from __future__ import annotations

import os
import signal
import time

import pytest

from sat_sim.interactive.models import (
    InteractiveSessionSpec, Telecommand, CommandState, SessionState,
)
from sat_sim.interactive.runtime_factory import build_persistent_runtime
from sat_sim.interactive.supervised_native_runtime import SupervisedContinuousNativeRuntime
from sat_sim.interactive.manager import InteractiveSession
from sat_sim.interactive.workspace import SessionWorkspace

CAPABILITY = "whole_spacecraft.unified_native.v1"


def _spec(session: str, *, continuous: bool = True):
    return InteractiveSessionSpec(
        session_id=session, capability_id=CAPABILITY,
        task_spec={
            "simulation": {
                "duration_s": 1.0, "sample_s": .2,
                "solver": {"step_s": .2},
            },
            "parameters": {"values": {}},
        },
        continuous=continuous, quantum_s=.2, paced=False,
        heartbeat_interval_s=1., idle_timeout_s=30.,
    )


def _tc(spec: InteractiveSessionSpec, revision=2):
    return Telecommand(
        command_id="r7b-set-target", session_id=spec.session_id,
        session_revision=revision, operation="adcs.target.set",
        target="subsystem.adcs",
        parameters={"sigma_rn": [0.1, 0.0, 0.0]},
        actor_id="pilot", actor_role="operator",
        execute_at_sim_time_s=.4,
    )


def test_feature_flag_default_remains_inprocess(monkeypatch):
    spec = _spec("r7b-default")
    monkeypatch.delenv("SAT_SIM_SUPERVISED_NATIVE_RUNTIME", raising=False)
    runtime, end = build_persistent_runtime(spec)
    assert runtime.__class__.__name__ == "UnifiedPersistentRuntime"
    assert end is None


def test_supervisor_selection_fails_closed_for_noncontinuous(monkeypatch):
    spec = _spec("r7b-invalid", continuous=False)
    monkeypatch.setenv("SAT_SIM_SUPERVISED_NATIVE_RUNTIME", "1")
    with pytest.raises(ValueError, match="SUPERVISED_NATIVE_ONLY_WHOLE_CONTINUOUS"):
        build_persistent_runtime(spec)


def test_adapter_must_be_explicitly_prepared_and_cannot_restart():
    runtime = SupervisedContinuousNativeRuntime(_spec("r7b-no-restart"))
    assert runtime.child_pid is None
    assert runtime.current_time_s == 0.
    with pytest.raises(RuntimeError, match="SESSION_FENCED"):
        runtime.advance_to(.2)
    runtime.abort()
    with pytest.raises(RuntimeError, match="REPREPARE_FORBIDDEN"):
        runtime.prepare()


def _real_samples(runtime, spec):
    runtime.prepare()
    assert runtime.current_time_s == pytest.approx(0.)
    rows = []
    for stop in (.2, .4, .6):
        if stop == .4:
            runtime.apply_command(_tc(spec))
        rows += list(runtime.advance_to(stop))
    result = runtime.finalize()
    return rows, result


def test_native_supervised_worker_matches_real_inprocess_basilisk(tmp_path):
    pytest.importorskip("Basilisk")
    spec = _spec("r7b-native-parity")
    base, _ = build_persistent_runtime(spec, supervised=False)
    expected_rows, _ = _real_samples(base, spec)
    child, end = build_persistent_runtime(spec, supervised=True)
    assert end is None
    try:
        assert isinstance(child, SupervisedContinuousNativeRuntime)
        rows, result = _real_samples(child, spec)
        assert child.child_pid != os.getpid()
        evidence = child.last_reap
        assert evidence is not None
        assert evidence["direct_child_join_observed"] is True
        assert evidence["exitcode"] == 0
        assert evidence["native_resume_supported"] is False
        assert evidence["automatic_tc_replay_allowed"] is False
        assert len(rows) == len(expected_rows) > 0
        for a, b in zip(rows, expected_rows, strict=True):
            assert a["time_s"] == b["time_s"]
            for key in ("orbit.radius_m", "adcs.pointing_error_deg", "eps.battery_soc"):
                if key in a and key in b:
                    assert a[key] == pytest.approx(b[key], rel=1e-9, abs=1e-8)
        assert result["summary"]["overall_status"] == "PASS"
        with pytest.raises(RuntimeError, match="SESSION_FENCED"):
            child.apply_command(_tc(spec))
    finally:
        child.abort()


def test_native_interactive_session_owns_tc_ledger_parent_and_graph_in_child(tmp_path):
    pytest.importorskip("Basilisk")
    spec = _spec("r7b-production-session")
    ws = SessionWorkspace(tmp_path, spec)
    runtime, end = build_persistent_runtime(spec, supervised=True)
    session = InteractiveSession(spec, runtime, end_time_s=end, workspace=ws)
    try:
        session.prepare()
        assert runtime.child_pid not in {None, os.getpid()}
        cmd = _tc(spec, revision=session.revision)
        assert session.submit_command(cmd).state is CommandState.QUEUED
        session.start()
        deadline = time.monotonic() + 30.
        while session.current_time_s < .8:
            assert session.state is not SessionState.FAILED, session.terminal_error
            assert time.monotonic() < deadline, "native worker failed to make progress"
            time.sleep(.02)
        session.pause()
        assert session.command_acks(cmd.command_id)[-1].state is CommandState.ACKED
        session.stop()
        assert session.wait(10.)
        assert session.state is SessionState.COMPLETED
        assert runtime.last_reap is not None
        assert runtime.last_reap["exitcode"] == 0
        assert session.snapshot().archive_status == "SEALED"
        assert list(SessionWorkspace.iter_hash_chain(ws.commands_path))[-1]["ack"]["state"] == "ACKED"
    finally:
        if session.state not in (SessionState.COMPLETED, SessionState.FAILED, SessionState.ABORTED):
            session.abort()
            session.wait(10.)


@pytest.mark.skipif(os.name != "posix", reason="SIGKILL direct-worker injection on POSIX")
def test_native_worker_death_does_not_recreate_graph_or_retry_tc():
    pytest.importorskip("Basilisk")
    spec = _spec("r7b-kill")
    worker, _ = build_persistent_runtime(spec, supervised=True)
    try:
        worker.prepare()
        pid = worker.child_pid
        assert pid is not None
        worker.advance_to(.2)
        os.kill(pid, signal.SIGKILL)
        with pytest.raises(RuntimeError, match="AMBIGUOUS"):
            worker.advance_to(.4)
        evidence = worker.last_reap
        assert evidence is not None
        assert evidence["child_pid"] == pid
        assert evidence["exitcode"] != 0
        assert evidence["native_resume_supported"] is False
        assert evidence["automatic_tc_replay_allowed"] is False
        with pytest.raises(RuntimeError, match="SESSION_FENCED"):
            worker.advance_to(.6)
        with pytest.raises(RuntimeError, match="REPREPARE_FORBIDDEN"):
            worker.prepare()
    finally:
        worker.abort()


@pytest.mark.skipif(os.name != "posix", reason="POSIX SIGKILL fault injection")
def test_live_session_child_crash_preserves_active_sqlite_owner_fence(tmp_path):
    import sqlite3
    pytest.importorskip("Basilisk")
    spec = _spec("r7b-owner-crash")
    ws = SessionWorkspace(tmp_path, spec)
    runtime, end = build_persistent_runtime(spec, supervised=True)
    session = InteractiveSession(spec, runtime, end_time_s=end, workspace=ws)
    try:
        session.prepare()
        cmd = _tc(spec, revision=session.revision)
        assert session.submit_command(cmd).state is CommandState.QUEUED
        # Crash while session paused/READY before any new native step.
        child_pid = runtime.child_pid
        assert child_pid is not None
        os.kill(child_pid, signal.SIGKILL)
        session.start()
        deadline = time.monotonic() + 15.
        while session.state not in {SessionState.FAILED, SessionState.ABORTED}:
            assert time.monotonic() < deadline, "crashed worker did not terminate session"
            time.sleep(.02)
        assert session.state is SessionState.FAILED
        assert session.wait(10.)
        assert runtime.last_reap is not None
        assert runtime.last_reap["exitcode"] != 0
        with sqlite3.connect(ws.path / "command_ids.sqlite") as db:
            state = db.execute(
                "SELECT owner_state FROM command_writer_epoch WHERE singleton=1"
            ).fetchone()
            assert state == ("ACTIVE",)
            # No ACKED state is invented for the interrupted command.
            status = db.execute(
                "SELECT state FROM command_lifecycle WHERE command_id=?",
                (cmd.command_id,),
            ).fetchone()[0]
            assert status in {"QUEUED", "EXECUTING"}
        from sat_sim.interactive.command_mailbox import DeterministicCommandMailbox
        with pytest.raises(RuntimeError, match="WRITER_ALREADY_ACTIVE"):
            DeterministicCommandMailbox(
                persistent_ledger=True,
                ledger_path=ws.path / "command_ids.sqlite",
            )
    finally:
        if session.state not in (SessionState.COMPLETED, SessionState.FAILED, SessionState.ABORTED):
            session.abort()
            session.wait(10.)


def test_native_graph_is_not_created_before_parent_fence_handshake(monkeypatch):
    """No Windows/other-platform Basilisk graph until parent says prepare."""
    import os
    import sat_sim.interactive.supervised_native_runtime as target
    import sat_sim.interactive.runtime_factory as factory

    order = []
    original_parent = os.getppid()
    monkeypatch.setattr(
        target, "arm_native_parent_death_fence",
        lambda pid: {"supported": True, "kind": "TEST_GUARD",
                     "parent_pid": pid},
    )

    class FakeGraph:
        current_time_s = 0.
        def prepare(self):
            order.append("native_prepare")
        def abort(self):
            order.append("native_abort")

    def construct(spec, *, supervised):
        assert supervised is False
        order.append("native_construct")
        return FakeGraph(), None

    monkeypatch.setattr(factory, "build_persistent_runtime", construct)

    class FakePipe:
        def __init__(self):
            self.acks = []
            self.commands = iter([
                {"schema": target._SCHEMA, "seq": 1, "nonce": "test-nonce",
                 "op": "prepare", "payload": None},
                {"schema": target._SCHEMA, "seq": 2, "nonce": "test-nonce",
                 "op": "abort", "payload": None},
            ])
        def send(self, message):
            self.acks.append(message)
            order.append("child_handshake" if message["seq"] == 0 else
                         "native_reply")
            if message["seq"] == 0:
                assert "native_construct" not in order
                assert message["pid"] == os.getpid()
                assert message["payload"]["parent_death_fence"]["supported"]
        def recv(self):
            order.append("parent_request")
            return next(self.commands)
        def close(self):
            pass

    pipe = FakePipe()
    target._native_process_main(
        pipe, _spec("r7d-fence-first").model_dump(mode="json"),
        "test-nonce", original_parent,
    )
    assert order[:3] == ["child_handshake", "parent_request",
                          "native_construct"]
    assert [ack["seq"] for ack in pipe.acks] == [0, 1, 2]
    assert pipe.acks[-1]["ok"] is True
