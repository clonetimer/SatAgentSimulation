"""R9-E: native whole-spacecraft clock and C FSW memory import/readback in fresh PID.

Test-only. The child never executes a physical timestep after restoring clock.
This establishes subset SWIG writeability, NOT integrator/subsystem restart.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from sat_sim.interactive.native_whole_clock_fsw_lab import (
    import_memento_fresh_lab, validate_memento,
)
from sat_sim.interactive.native_whole_state_import_lab import (
    import_fresh_native_whole_state_subset,
)


class _ProbeDone(Exception):
    def __init__(self, result):
        super().__init__("R9E_READBACK_COMPLETE")
        self.result = result


def _worker(checkpoint_path: str, topology: str) -> None:
    from sat_sim.bsk_engine.unified_native import (
        UnifiedNativeRuntime, UnifiedRuntimeConfig, WHOLE_UNIFIED_CAPABILITY_ID,
    )
    data = json.loads(Path(checkpoint_path).read_text(encoding="utf-8"))
    original = data["source_audit"]
    memento = original["native_clock_fsw_lab"]

    def probe(*, sim, process, task, spacecraft, rw_effector, controller,
              wheel_count, whole_modules=None,
              rw_command_fault_manager=None, allocator=None):
        # Check the official native spacecraft/RW subset first. That operation
        # requires a fresh 0ns Basilisk scheduler. Neither probe ever runs it.
        dynamics = import_fresh_native_whole_state_subset(
            sim=sim, spacecraft=spacecraft, expected_rw_count=wheel_count,
            checkpoint_audit=original,
        )
        phase = import_memento_fresh_lab(
            sim=sim, process=process, task=task,
            spacecraft=spacecraft, controller=controller,
            checkpoint=memento, source_audit=original,
        )
        raise _ProbeDone({"dynamics": dynamics, "phase": phase})

    cfg = UnifiedRuntimeConfig(
        capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
        duration_s=0.4, step_s=0.2, sample_s=0.2, adcs_only=False,
        values={"rw_configuration": topology}, events=(),
    )
    try:
        UnifiedNativeRuntime(
            cfg, _test_only_fresh_native_graph_probe=probe,
        ).run()
    except _ProbeDone as done:
        print("R9E_WHOLE_NATIVE_MEMORY_JSON:" + json.dumps({
            "source_pid": data["source_pid"], "child_pid": os.getpid(),
            "readback": done.result,
        }, sort_keys=True), flush=True)
        return
    raise RuntimeError("R9E_NATIVE_IMPORT_PROBE_NOT_EXECUTED")


@pytest.mark.parametrize("topology,number", [
    ("orthogonal_3", 3), ("pyramid_4", 4),
])
def test_r9e_real_whole_native_clock_and_fsw_memory_writeback(
    tmp_path, topology, number,
):
    pytest.importorskip("Basilisk")
    from sat_sim.bsk_engine.unified_native import UnifiedRuntimeConfig
    from sat_sim.interactive.unified_runtime import UnifiedPersistentRuntime
    from sat_sim.interactive.native_checkpoint_audit import verify_coverage_report
    from sat_sim.interactive.native_recovery_readiness import (
        build_recovery_readiness, verify_recovery_readiness,
    )
    runtime = UnifiedPersistentRuntime(UnifiedRuntimeConfig(
        capability_id="whole_spacecraft.unified_native.v1",
        duration_s=0.5, step_s=0.2, sample_s=0.2,
        adcs_only=False, values={"rw_configuration": topology},
        events=(), interactive_unbounded=True,
    ), continuous=True)
    runtime.prepare()
    try:
        runtime.advance_to(0.8)
        evidence = runtime.audit_native_checkpoint_coverage()
        verify_coverage_report(evidence)
        memento = evidence["native_clock_fsw_lab"]
        assert memento is not None, evidence["unavailable_observations"]
        assert memento["clock"]["current_ns"] == 800_000_000
        assert memento["fsw"]["cached_rw_count"] == number
        validate_memento(
            memento, source_time_ns=800_000_000,
            expected_rw_count=number,
            expected_fsw=evidence["native_adcs_owner_observations"]["fsw_memory"],
        )
        gate = build_recovery_readiness(evidence)
        verify_recovery_readiness(gate)
        assert gate["native_clock_fsw_lab_present"] is True
        assert gate["restart_readiness"] == "BLOCKED"
        assert gate["restorable_owner_count"] == 0
        assert gate["automatic_resume_allowed"] is False
        assert "native_clock_fsw_lab" in gate["owners"][0]["observation_keys"]
        assert "native_clock_fsw_lab" in gate["owners"][3]["observation_keys"]

        # A second read at the same paused native time is purely observational.
        repeated = runtime.audit_native_checkpoint_coverage()
        assert repeated["native_clock_fsw_lab"] == memento
        assert runtime.current_time_s == pytest.approx(0.8)

        path = tmp_path / f"r9e-{topology}.json"
        path.write_text(json.dumps({
            "source_pid": os.getpid(), "source_audit": evidence,
        }, sort_keys=True), encoding="utf-8")
        child = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()),
             "--r9e-worker", str(path), topology],
            capture_output=True, text=True, timeout=60, check=False,
        )
        assert child.returncode == 0, child.stdout + "\n" + child.stderr
        lines = [line.removeprefix("R9E_WHOLE_NATIVE_MEMORY_JSON:")
                 for line in child.stdout.splitlines()
                 if line.startswith("R9E_WHOLE_NATIVE_MEMORY_JSON:")]
        assert len(lines) == 1
        report = json.loads(lines[0])
        assert report["source_pid"] == os.getpid()
        assert report["child_pid"] != os.getpid()
        imp = report["readback"]
        assert imp["dynamics"]["native_vectors_readback"] == (
            evidence["native_state_subset_pilot"]["vectors"]
        )
        assert imp["phase"]["native_clock_readback"] == memento["clock"]
        assert imp["phase"]["native_fsw_readback"] == memento["fsw"]
        assert imp["phase"]["fresh_time_before_import_ns"] == 0
        assert imp["phase"]["clock_and_fsw_subset_imported"] is True
        assert imp["phase"]["integrator_hidden_stages_restored"] is False
        assert imp["phase"]["complete_scheduler_event_queues_restored"] is False
        assert imp["phase"]["other_owner_states_restored"] is False
        assert imp["phase"]["whole_spacecraft_parity_proven"] is False
        assert imp["phase"]["resume_supported"] is False

        # A different RW topology is denied by the existing source owner gate.
        opposite = "orthogonal_3" if number == 4 else "pyramid_4"
        invalid = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()),
             "--r9e-worker", str(path), opposite],
            capture_output=True, text=True, timeout=60, check=False,
        )
        assert invalid.returncode != 0
        assert "R9D_ADCS_TOPOLOGY_MISMATCH" in invalid.stderr
        assert "R9E_WHOLE_NATIVE_MEMORY_JSON" not in invalid.stdout

        runtime.advance_to(1.2)
        later = runtime.audit_native_checkpoint_coverage()
        assert later["native_clock_fsw_lab"]["clock"]["current_ns"] == 1_200_000_000
        runtime.finalize()
    finally:
        if runtime._thread is not None and runtime._thread.is_alive():
            runtime.abort()


def test_r9e_memento_fails_even_after_rehashed_false_resume_or_clock_forgery():
    # This is a validator contract test; never forge physical evidence.
    source = {
        "schema": "sat-native-whole-clock-fsw-memento-lab.v1",
        "scope": "WHOLE_GRAPH_SINGLE_PROCESS_SINGLE_TASK_PRETICK_LAB",
        "clock": {
            "current_ns": 800_000_000, "next_ns": 1_000_000_000,
            "process_name": "UnifiedNativeProcess", "process_priority": -1,
            "process_active": True, "process_next_ns": 1_000_000_000,
            "process_route_ns": 0, "process_task_start_ns": 1_000_000_000,
            "process_task_period_ns": 200_000_000, "process_task_priority": -1,
            "task_first_ns": 0, "task_pickup_ns": 200_000_000,
            "task_next_ns": 1_000_000_000, "task_period_ns": 200_000_000,
            "hub_time_before_s": 0.8, "hub_time_before_ns": 800_000_000,
            "hub_step_s": 0.2,
        },
        "fsw": {
            "cached_rw_count": 3, "prior_time_ns": 800_000_000,
            "K": 3.0, "P": 10.0, "Ki": 0.0, "integral_limit": 1.0,
            "int_sigma": [0.1, 0.0, 0.0], "z": [0.0, 0.0, 0.0],
        },
        "test_only": True, "private_states_complete": False,
        "integrator_restored": False, "message_history_restored": False,
        "whole_resume_supported": False, "fresh_process_parity_proven": False,
    }
    def signed(doc):
        doc["sha256"] = hashlib.sha256(json.dumps({
            k: v for k, v in doc.items() if k != "sha256"
        }, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        return doc
    validate_memento(signed(source), source_time_ns=800_000_000,
                     expected_rw_count=3)
    tampered = copy.deepcopy(source)
    tampered["whole_resume_supported"] = True
    with pytest.raises(ValueError, match="R9E_MEMENTO_FALSE_RESTORE_CLAIM"):
        validate_memento(signed(tampered))
    tampered = copy.deepcopy(source)
    tampered["fsw"]["cached_rw_count"] = 4
    with pytest.raises(ValueError, match="R9E_FSW_TOPOLOGY_INVALID"):
        validate_memento(signed(tampered), expected_rw_count=3)
    tampered = copy.deepcopy(source)
    tampered["clock"]["current_ns"] = 900_000_000
    with pytest.raises(ValueError, match="R9E_CLOCK_PHASE_INVALID|R9E_CLOCK_HUB_TIME_INVALID"):
        validate_memento(signed(tampered), source_time_ns=800_000_000)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--r9e-worker":
        _worker(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit("unsupported isolated whole-clock-FSW test profile")
