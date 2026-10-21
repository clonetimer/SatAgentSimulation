"""R7a: native subprocess termination proof before any test-only rehydration.

The source graph runs *in a held child process* and is killed and reaped
before a fresh OS process reconstructs the specific R5 Basilisk/3RW graph.
There is deliberately NO production SessionWorkspace native resume API.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from sat_sim.interactive.native_process_supervisor import (
    DirectChildSupervisor, _digest, verify_reap_barrier, write_child_ready,
)

HERE = Path(__file__).resolve()
JOINT_WORKER = HERE.with_name("test_native_joint_checkpoint.py")


def _child_worker(profile: str, ready_path: Path, artifact_dir: Path) -> None:
    if profile == "stub-worker":
        write_child_ready(
            ready_path, profile=profile, checkpoint_ns=0,
            checkpoint_sha256=None,
        )
    elif profile == "adcs-r5-joint-source":
        # Import Basilisk ONLY in the child or native-marked parent test,
        # never in the pure process-barrier guard tests.
        from test_native_joint_checkpoint import _make_joint_source

        artifact_dir.mkdir(parents=True, exist_ok=True)
        graph, journal, joint = _make_joint_source(artifact_dir)
        wrapper = {
            "joint": joint,
            "last_rw_motor_payload": [
                float(x)
                for x in graph["allocator"].rwMotorTorqueOutMsg.read().motorTorque
            ],
        }
        checkpoint = artifact_dir / "supervised-r5-checkpoint.json"
        checkpoint.write_text(
            json.dumps(wrapper, sort_keys=True), encoding="utf-8",
        )
        with checkpoint.open("rb") as handle:
            os.fsync(handle.fileno())
        checkpoint_sha = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        write_child_ready(
            ready_path, profile=profile,
            checkpoint_ns=joint["checkpoint_ns"],
            checkpoint_sha256=checkpoint_sha,
        )
    else:
        raise ValueError("BAD_NATIVE_CHILD_PROFILE")
    # Child remains alive with its original Basilisk object graph intact
    # until the parent, holding the exact Popen handle, terminates it.
    while True:
        time.sleep(1.)


def _launch(tmp_path: Path, *, profile: str = "stub-worker") -> DirectChildSupervisor:
    return DirectChildSupervisor.launch(
        [sys.executable, str(HERE), "--r7-worker", profile,
         str(tmp_path / "ready.json"), str(tmp_path)],
        ready_path=tmp_path / "ready.json",
        barrier_path=tmp_path / "reaped-barrier.json",
        profile=profile,
    )


def test_direct_child_must_be_reaped_before_barrier_exists(tmp_path):
    supervisor = _launch(tmp_path)
    try:
        with pytest.raises(ValueError, match="NO_READY_HANDSHAKE"):
            supervisor.terminate_and_attest()
        assert not supervisor.barrier_path.exists()
        ready = supervisor.observe_ready(timeout_s=8.)
        assert ready["pid"] == supervisor.pid
        assert supervisor.returncode is None
        assert not supervisor.barrier_path.exists()
        barrier = supervisor.terminate_and_attest(force=True)
        assert supervisor.returncode != 0
        assert barrier["pid"] == supervisor.pid
        assert barrier["direct_child_wait_observed"] is True
        assert barrier["process_tree_quiescence_proven"] is False
        assert barrier["native_resume_supported"] is False
        assert barrier["automatic_tc_replay_allowed"] is False
        with pytest.raises(ValueError, match="ALREADY_ATTESTED"):
            supervisor.terminate_and_attest()
    finally:
        supervisor.cleanup_without_attestation()


def test_death_barrier_digest_nonce_pid_and_false_claims(tmp_path):
    supervisor = _launch(tmp_path)
    try:
        supervisor.observe_ready(timeout_s=8.)
        supervisor.terminate_and_attest(force=True)
        original = supervisor.barrier_path.read_text(encoding="utf-8")
        with pytest.raises(ValueError, match="CHILD_ID_MISMATCH"):
            verify_reap_barrier(supervisor.barrier_path, expected_pid=supervisor.pid + 1)
        with pytest.raises(ValueError, match="NONCE_MISMATCH"):
            verify_reap_barrier(supervisor.barrier_path, expected_nonce="0" * 64)
        tampered = json.loads(original)
        tampered["native_resume_supported"] = True
        supervisor.barrier_path.write_text(json.dumps(tampered), encoding="utf-8")
        with pytest.raises(ValueError, match="DIGEST_INVALID"):
            verify_reap_barrier(supervisor.barrier_path)
        tampered["digest_sha256"] = _digest({
            k: v for k, v in tampered.items() if k != "digest_sha256"
        })
        supervisor.barrier_path.write_text(json.dumps(tampered), encoding="utf-8")
        with pytest.raises(ValueError, match="UNSUPPORTED_CLAIM"):
            verify_reap_barrier(supervisor.barrier_path)
        supervisor.barrier_path.write_text(original, encoding="utf-8")
        supervisor.barrier_path.with_suffix(".pending").write_text("partial")
        with pytest.raises(ValueError, match="STAGING_FOUND"):
            verify_reap_barrier(supervisor.barrier_path)
    finally:
        supervisor.cleanup_without_attestation()


def test_ready_missing_from_dead_worker_must_not_generate_reap_barrier(tmp_path):
    supervisor = DirectChildSupervisor.launch(
        [sys.executable, "-c", "import sys; sys.exit(23)"],
        ready_path=tmp_path / "ready.json",
        barrier_path=tmp_path / "reaped-barrier.json",
        profile="stub-worker",
    )
    try:
        with pytest.raises(RuntimeError, match="CHILD_EXITED_BEFORE_READY"):
            supervisor.observe_ready(timeout_s=5.)
        assert not supervisor.barrier_path.exists()
    finally:
        supervisor.cleanup_without_attestation()


def test_native_basilisk_reap_actual_adcs_child_before_r5_two_process_recovery(tmp_path):
    pytest.importorskip("Basilisk")
    from test_native_joint_checkpoint import _make_joint_source, _continue
    from sat_sim.interactive.native_joint_checkpoint import verify_joint_checkpoint

    # Independent deterministic baseline that is never interrupted.
    baseline_root = tmp_path / "uninterrupted-reference"
    baseline_root.mkdir()
    base_graph, base_journal, _ = _make_joint_source(baseline_root)
    reference = _continue(base_graph, base_journal)

    child_root = tmp_path / "child-native-source"
    child_root.mkdir()
    supervisor = _launch(child_root, profile="adcs-r5-joint-source")
    try:
        ready = supervisor.observe_ready(timeout_s=40.)
        checkpoint = child_root / "supervised-r5-checkpoint.json"
        tc_journal = child_root / "source-tc-journal.json"
        assert checkpoint.is_file() and tc_journal.is_file()
        observed_digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        assert ready["checkpoint_sha256"] == observed_digest
        wrapper = json.loads(checkpoint.read_text(encoding="utf-8"))
        checked = verify_joint_checkpoint(wrapper["joint"])
        assert checked["checkpoint_ns"] == 2_000_000_000
        assert checked["resume_supported"] is False
        # At this point the original Basilisk graph is still running in its
        # original held Python OS worker. An offline checkpoint digest alone
        # must NOT be mistaken for a process termination attestation.
        assert supervisor.returncode is None
        assert not supervisor.barrier_path.exists()

        barrier = supervisor.terminate_and_attest(force=True)
        assert barrier["profile"] == "adcs-r5-joint-source"
        assert barrier["checkpoint_sha256"] == observed_digest
        verify_reap_barrier(
            supervisor.barrier_path,
            expected_pid=supervisor.pid,
            expected_checkpoint_sha256=observed_digest,
        )

        # This is explicitly a controlled native R5 *pilot* restart, NOT
        # activation of the original production SessionWorkspace queue.
        run = subprocess.run(
            [sys.executable, str(JOINT_WORKER), "--joint-worker",
             str(checkpoint), str(tc_journal), "none"],
            capture_output=True, text=True, timeout=50, check=False,
        )
        assert run.returncode == 0, run.stdout + "\n" + run.stderr
        restored = json.loads(next(
            line.removeprefix("JOINT_ADCS_RESTART_JSON:")
            for line in run.stdout.splitlines()
            if line.startswith("JOINT_ADCS_RESTART_JSON:")
        ))
        assert restored["pid"] != supervisor.pid
        assert restored["resume_supported"] is False
        assert restored["end_ns"] == reference["end_ns"] == 5_000_000_000
        assert restored["tc_state"] == reference["tc_state"] == "ACKED"
        for channel in (
            "attitude_mrp", "body_rate", "fsw_torque",
            "motor_torque", "rw_speeds",
        ):
            rows = restored["telemetry"][channel]
            truth = reference["telemetry"][channel]
            assert [t for t, _ in rows] == [t for t, _ in truth], channel
            for (_, lhs), (_, rhs) in zip(rows, truth):
                assert lhs == pytest.approx(rhs, abs=1e-8, rel=1e-11), channel
        for key, vector in reference["final_state"]["vectors"].items():
            assert restored["final_state"]["vectors"][key] == pytest.approx(
                vector, abs=1e-8, rel=1e-11,
            ), key
    finally:
        supervisor.cleanup_without_attestation()


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--r7-worker":
        _child_worker(sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]))
    else:
        raise SystemExit("R7 pilot worker is not a production native restore service")
