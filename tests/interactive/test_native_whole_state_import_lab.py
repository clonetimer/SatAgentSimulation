"""R9-D genuine Basilisk whole-graph *partial* StateData import in a new OS PID.

Source is an uninterrupted live full spacecraft with EPS, thermal and comm.
Fresh child constructs the SAME unified whole-spacecraft model, imports only
the native hub/RW StateData vectors at t=0, verifies readback then STOPs
before executing any physics step. This is NOT restart parity or TC replay.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from sat_sim.interactive.native_whole_state_import_lab import (
    import_fresh_native_whole_state_subset, PROFILE, REMAINS_UNRESTORED,
)


class _LabProbeComplete(Exception):
    def __init__(self, report):
        super().__init__("R9D_TEST_ONLY_NATIVE_GRAPH_PROBE_COMPLETE")
        self.report = report


def _child_worker(path: str, topology: str) -> None:
    from sat_sim.bsk_engine.unified_native import (
        UnifiedNativeRuntime, UnifiedRuntimeConfig, WHOLE_UNIFIED_CAPABILITY_ID,
    )
    evidence = json.loads(Path(path).read_text(encoding="utf-8"))
    checkpoint = evidence["checkpoint_audit"]

    def probe(*, sim, spacecraft, rw_effector, wheel_count,
              process=None, task=None, controller=None,
              whole_modules=None):
        assert len(rw_effector.ReactionWheelData) == wheel_count
        report = import_fresh_native_whole_state_subset(
            sim=sim, spacecraft=spacecraft,
            expected_rw_count=wheel_count, checkpoint_audit=checkpoint,
        )
        # Stop before the first dynamics/FSW/EPS/thermal/comm task runs:
        # the partial import MUST NOT be represented as a valid continuation.
        raise _LabProbeComplete(report)

    config = UnifiedRuntimeConfig(
        capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
        duration_s=0.4, step_s=0.2, sample_s=0.2, adcs_only=False,
        values={"rw_configuration": topology}, events=(),
    )
    try:
        UnifiedNativeRuntime(
            config, _test_only_fresh_native_graph_probe=probe,
        ).run()
    except _LabProbeComplete as done:
        print("R9D_FRESH_WHOLE_READBACK_JSON:" + json.dumps({
            "pid": os.getpid(), "profile": PROFILE,
            "source_pid": evidence["source_pid"],
            "import_report": done.report,
        }, sort_keys=True), flush=True)
        return
    raise RuntimeError("R9D_FRESH_WHOLE_PROBE_DID_NOT_RUN")


@pytest.mark.parametrize("topology,count", [("orthogonal_3", 3), ("pyramid_4", 4)])
def test_r9d_native_fresh_os_process_whole_graph_import_readback_only(
    tmp_path, topology, count,
) -> None:
    pytest.importorskip("Basilisk")
    from sat_sim.bsk_engine.unified_native import UnifiedRuntimeConfig
    from sat_sim.interactive.unified_runtime import UnifiedPersistentRuntime
    from sat_sim.interactive.native_checkpoint_audit import verify_coverage_report
    from sat_sim.interactive.native_recovery_readiness import build_recovery_readiness

    parent = UnifiedPersistentRuntime(UnifiedRuntimeConfig(
        capability_id="whole_spacecraft.unified_native.v1",
        duration_s=0.5, step_s=0.2, sample_s=0.2,
        adcs_only=False, values={"rw_configuration": topology},
        events=(), interactive_unbounded=True,
    ), continuous=True)
    parent.prepare()
    try:
        parent.advance_to(0.8)
        checkpoint = parent.audit_native_checkpoint_coverage()
        verify_coverage_report(checkpoint)
        assert checkpoint["native_joint_owner_observations"] is not None
        assert checkpoint["native_adcs_owner_observations"]["rw_count"] == count
        assert build_recovery_readiness(checkpoint)["restart_readiness"] == "BLOCKED"
        file = tmp_path / f"r9d-{topology}.json"
        file.write_text(json.dumps({
            "source_pid": os.getpid(), "checkpoint_audit": checkpoint,
        }, sort_keys=True), encoding="utf-8")

        worker = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--r9d-worker",
             str(file), topology],
            capture_output=True, text=True, timeout=60, check=False,
        )
        assert worker.returncode == 0, worker.stdout + "\n" + worker.stderr
        results = [
            line.removeprefix("R9D_FRESH_WHOLE_READBACK_JSON:")
            for line in worker.stdout.splitlines()
            if line.startswith("R9D_FRESH_WHOLE_READBACK_JSON:")
        ]
        assert len(results) == 1
        fresh = json.loads(results[0])
        assert fresh["pid"] != os.getpid()
        assert fresh["source_pid"] == os.getpid()
        r = fresh["import_report"]
        assert r["profile"] == PROFILE
        assert r["rw_count"] == count
        assert r["source_sim_time_ns"] == 800_000_000
        assert r["fresh_scheduler_time_ns"] == 0
        assert r["source_audit_sha256"] == checkpoint["report_sha256"]
        assert r["native_state_subset_imported"] is True
        assert r["native_vectors_readback"] == (
            checkpoint["native_state_subset_pilot"]["vectors"]
        )
        assert len(r["native_vectors_readback"]["reaction_wheel_speed_rad_s"]) == count
        assert r["native_vectors_before"]["hub_position_m"] != (
            r["native_vectors_readback"]["hub_position_m"]
        )
        assert r["new_process_scheduler_rehydrated"] is False
        assert r["full_native_integrator_rehydrated"] is False
        assert r["whole_spacecraft_physical_parity_proven"] is False
        assert r["automatic_resume_allowed"] is False
        assert r["automatic_tc_replay_allowed"] is False
        assert r["resume_supported"] is False
        assert r["not_restored"] == list(REMAINS_UNRESTORED)

        # Continue only the ORIGINAL full spacecraft, proving that source
        # and imported child are not treated as the same synchronized session.
        parent.advance_to(1.2)
        continuing = parent.audit_native_checkpoint_coverage()
        assert continuing["native_state_subset_pilot"]["vectors"]["hub_position_m"] != (
            r["native_vectors_readback"]["hub_position_m"]
        )
        assert continuing["native_joint_owner_observations"]["capture_time_ns"] == 1_200_000_000

        # A dimension mismatch must fail before the first writable native
        # state operation in the new graph.
        opposite = "orthogonal_3" if count == 4 else "pyramid_4"
        invalid = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--r9d-worker",
             str(file), opposite],
            capture_output=True, text=True, timeout=60, check=False,
        )
        assert invalid.returncode != 0
        assert "R9D_ADCS_TOPOLOGY_MISMATCH" in invalid.stderr
        assert "R9D_FRESH_WHOLE_READBACK_JSON:" not in invalid.stdout
        parent.finalize()
    finally:
        if parent._thread is not None and parent._thread.is_alive():
            parent.abort()


def test_r9d_rejects_rehashed_restart_claim_without_mutating_any_state():
    from test_native_recovery_readiness import _audit_payload
    import hashlib
    source = _audit_payload()
    # This valid, signed R8 coverage fixture has only observations, not
    # the real multi-owner whole-graph source required by R9-D.
    with pytest.raises(ValueError, match="R9D_WHOLE_GRAPH_OWNER_EVIDENCE_REQUIRED"):
        import_fresh_native_whole_state_subset(
            sim=object(), spacecraft=object(), expected_rw_count=3,
            checkpoint_audit=source,
        )
    forged = copy.deepcopy(source)
    forged["resume_supported"] = True
    forged["report_sha256"] = hashlib.sha256(json.dumps({
        key: value for key, value in forged.items() if key != "report_sha256"
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode()).hexdigest()
    with pytest.raises(ValueError, match="NATIVE_CHECKPOINT_AUDIT_UNSUPPORTED_RESUME_CLAIM"):
        import_fresh_native_whole_state_subset(
            sim=object(), spacecraft=object(), expected_rw_count=3,
            checkpoint_audit=forged,
        )


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--r9d-worker":
        _child_worker(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit("unsupported standalone test profile")
