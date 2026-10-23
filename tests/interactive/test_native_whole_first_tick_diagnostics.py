"""R9-F: isolated Basilisk first-tick negative-control for whole restart.

Experimental child in a separate OS PID imports *only* the R9-D/E approved
native subsets and runs exactly one native scheduler tick. It is never joined
to the production live session and NEVER has TC replay authority. The purpose
is to measure and document parity gaps, not to certify a whole restart.
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

from sat_sim.interactive.native_whole_first_tick_diagnostics import (
    build_divergence_report, verify_divergence_report, _digest,
)


def _measurement(time_ns, vectors, joint):
    return {
        "time_ns": int(time_ns),
        "vectors": vectors,
        "eps": {"battery_energy_j": joint["eps"]["battery_energy_j"]},
        "thermal": {"payload_temp_k": joint["thermal"]["nodes"]["payload"]["temp_k"]},
        "comm": {"storage_bits": joint["comm_data"]["storage_bits"]},
    }


def _worker(path: str, topology: str) -> None:
    from Basilisk.utilities import macros
    from sat_sim.bsk_engine.unified_native import (
        UnifiedNativeRuntime, UnifiedRuntimeConfig, WHOLE_UNIFIED_CAPABILITY_ID,
    )
    from sat_sim.interactive.native_state_pilot import capture_state_subset
    from sat_sim.interactive.native_checkpoint_audit import capture_scheduler_phase
    from sat_sim.interactive.native_joint_owner_observation import (
        capture_joint_owner_observation,
    )
    from sat_sim.interactive.native_whole_clock_fsw_lab import import_memento_fresh_lab
    from sat_sim.interactive.native_whole_state_import_lab import (
        import_fresh_native_whole_state_subset,
    )
    from sat_sim.interactive.native_closed_loop_evidence import (
        restore_rw_effector_memory_fresh,
        capture_rw_effector_memory,
    )
    from sat_sim.interactive.native_whole_applied_command_lab import (
        restore_last_applied_rw_command_fresh,
    )
    from sat_sim.interactive.native_battery_reset_barrier import (
        seed_battery_via_public_reset_lab,
    )
    evidence = json.loads(Path(path).read_text(encoding="utf-8"))
    source = evidence["checkpoint"]
    measurement_ns = int(evidence["comparison_ns"])
    all_stops = [int(t) for t in evidence.get("comparison_times_ns", [measurement_ns])]
    if (
        not 1 <= len(all_stops) <= 5
        or all_stops[0] != measurement_ns
        or all_stops != sorted(set(all_stops))
        or any(t != 800_000_000 + (i+1) * 200_000_000 for i, t in enumerate(all_stops))
    ):
        raise ValueError("R9H_COMPARISON_GRID_UNSUPPORTED")
    ablation_mode = evidence.get("ablation_mode", "R9F_NATIVE_SUBSET_ONLY")
    if ablation_mode not in (
        "R9F_NATIVE_SUBSET_ONLY", "R9G_WITH_RW_CPP_MEMORY",
        "R9H_RW_MEMORY_PLUS_APPLIED_COMMAND",
        "R9I_RW_APPLIED_AND_BATTERY_PUBLIC_RESET",
    ):
        raise ValueError("R9H_ABLATION_MODE_UNSUPPORTED")
    data = {"measurements": []}

    def probe(*, sim, process, task, spacecraft, rw_effector, controller,
              wheel_count, whole_modules, rw_command_fault_manager=None,
              allocator=None):
        native_vectors = import_fresh_native_whole_state_subset(
            sim=sim, spacecraft=spacecraft, expected_rw_count=wheel_count,
            checkpoint_audit=source,
        )
        native_clock_fsw = import_memento_fresh_lab(
            sim=sim, process=process, task=task,
            spacecraft=spacecraft, controller=controller,
            checkpoint=source["native_clock_fsw_lab"],
            source_audit=source,
        )
        assert native_vectors["native_state_subset_imported"] is True
        assert native_clock_fsw["clock_and_fsw_subset_imported"] is True
        assert int(sim.TotalSim.CurrentNanos) == int(
            source["native_clock_fsw_lab"]["clock"]["current_ns"]
        )
        assert measurement_ns > sim.TotalSim.CurrentNanos
        if ablation_mode in (
            "R9G_WITH_RW_CPP_MEMORY", "R9H_RW_MEMORY_PLUS_APPLIED_COMMAND",
            "R9I_RW_APPLIED_AND_BATTERY_PUBLIC_RESET",
        ):
            # R9-G adds only the genuine ReactionWheelStateEffector C++-held
            # memory visible in the source audit. Do NOT restore commands,
            # FSW message caches or anything synthetic in this experiment.
            # This occurs in the private pre-tick probe only.
            expected = source["native_adcs_owner_observations"]["native_rw_cpp_memory"]
            assert len(expected) == wheel_count
            restore_rw_effector_memory_fresh(rw_effector, expected)
            assert capture_rw_effector_memory(rw_effector) == expected
        if ablation_mode in (
            "R9H_RW_MEMORY_PLUS_APPLIED_COMMAND",
            "R9I_RW_APPLIED_AND_BATTERY_PUBLIC_RESET",
        ):
            assert rw_command_fault_manager is not None
            result = restore_last_applied_rw_command_fresh(
                applied_command_manager=rw_command_fault_manager,
                source_audit=source, expected_rw_count=wheel_count,
                target_clock_ns=int(sim.TotalSim.CurrentNanos),
            )
            assert result["single_message_writeback"] is True
            assert result["message_history_restored"] is False
            data["writeback"] = result
        if ablation_mode == "R9I_RW_APPLIED_AND_BATTERY_PUBLIC_RESET":
            # This is NOT restoration of protected SimpleBattery C++ memory.
            # Public Reset() forces internal previousTime to 0. Compare the
            # real resulting power trajectories in independent processes.
            data["battery_reset_control"] = seed_battery_via_public_reset_lab(
                battery=whole_modules["battery"], source_audit=source,
                expected_clock_ns=source["native_joint_owner_observations"]["capture_time_ns"],
                target_clock_ns=int(sim.TotalSim.CurrentNanos),
            )
            assert data["battery_reset_control"]["battery_full_state_restored"] is False
        # STRICTLY SCIENTIFICALLY INCOMPLETE LAB CONTINUATION. Every stop is
        # performed by genuine Basilisk dynamics, never by Python propagation.
        for stop_ns in all_stops:
            sim.ConfigureStopTime(stop_ns)
            sim.ExecuteSimulation()
            phase = capture_scheduler_phase(sim)
            assert phase["current_nanos"] == stop_ns
            subset = capture_state_subset(spacecraft, expected_rw_count=wheel_count)
            joint = capture_joint_owner_observation(
                scheduler_phase=phase,
                battery=whole_modules["battery"], panel=whole_modules["panel"],
                pdu=whole_modules["pdu"],
                thermal_graph=whole_modules["thermal_graph"],
                storage=whole_modules["storage"],
                downlink=whole_modules["native_downlink"],
            )
            data["measurements"].append(_measurement(
                stop_ns, subset["vectors"], joint,
            ))
        data["measurement"] = data["measurements"][0]
        # This child cannot be adopted as a resumed session.
        raise _LabMeasured()

    cfg = UnifiedRuntimeConfig(
        capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
        duration_s=0.4, step_s=0.2, sample_s=0.2,
        adcs_only=False, values={"rw_configuration": topology},
        events=(),
    )
    try:
        UnifiedNativeRuntime(
            cfg, _test_only_fresh_native_graph_probe=probe,
        ).run()
    except _LabMeasured:
        print("R9F_POST_IMPORT_JSON:" + json.dumps({
            "source_pid": evidence["source_pid"],
            "child_pid": os.getpid(),
            "measurement": data["measurement"],
            "measurements": data["measurements"],
            "writeback": data.get("writeback"),
            "battery_reset_control": data.get("battery_reset_control"),
            "ablation_mode": ablation_mode,
            "whole_resume_supported": False,
            "automatic_tc_replay_allowed": False,
        }, sort_keys=True, allow_nan=False), flush=True)
        return
    raise RuntimeError("R9F_UNSAFE_CHILD_NOT_INTERCEPTED")


class _LabMeasured(Exception):
    pass


@pytest.mark.parametrize("topology,wheel_count", [
    ("orthogonal_3", 3), ("pyramid_4", 4),
])
def test_r9f_real_basilisk_first_tick_negative_control(
    tmp_path, topology, wheel_count,
):
    pytest.importorskip("Basilisk")
    from sat_sim.bsk_engine.unified_native import UnifiedRuntimeConfig
    from sat_sim.interactive.unified_runtime import UnifiedPersistentRuntime
    from sat_sim.interactive.native_checkpoint_audit import verify_coverage_report
    from sat_sim.interactive.native_recovery_readiness import (
        build_recovery_readiness,
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
        checkpoint = runtime.audit_native_checkpoint_coverage()
        verify_coverage_report(checkpoint)
        assert checkpoint["native_clock_fsw_lab"] is not None
        assert checkpoint["native_adcs_owner_observations"]["rw_count"] == wheel_count
        assert build_recovery_readiness(checkpoint)["automatic_resume_allowed"] is False

        source_pid = os.getpid()
        path = tmp_path / f"R9F_{topology}.json"
        compare_ns = 1_000_000_000
        path.write_text(json.dumps({
            "source_pid": source_pid,
            "checkpoint": checkpoint,
            "comparison_ns": compare_ns,
        }, sort_keys=True, allow_nan=False), encoding="utf-8")
        # Continue the ORIGINAL uninterrupted, real unified whole graph.
        runtime.advance_to(1.0)
        source_after = runtime.audit_native_checkpoint_coverage()
        verify_coverage_report(source_after)
        source_measurement = _measurement(
            compare_ns, source_after["native_state_subset_pilot"]["vectors"],
            source_after["native_joint_owner_observations"],
        )

        child = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()),
             "--r9f-worker", str(path), topology],
            capture_output=True, text=True, timeout=70, check=False,
        )
        assert child.returncode == 0, child.stdout + "\n" + child.stderr
        rows = [
            line.removeprefix("R9F_POST_IMPORT_JSON:")
            for line in child.stdout.splitlines()
            if line.startswith("R9F_POST_IMPORT_JSON:")
        ]
        assert len(rows) == 1
        result = json.loads(rows[0])
        assert result["child_pid"] != source_pid
        assert result["source_pid"] == source_pid
        assert result["whole_resume_supported"] is False
        assert result["automatic_tc_replay_allowed"] is False

        compared = build_divergence_report(
            uninterrupted=source_measurement,
            imported_child=result["measurement"],
            source_pid=source_pid, child_pid=result["child_pid"],
            checkpoint_ns=800_000_000,
        )
        verify_divergence_report(compared)
        assert compared["comparison_time_ns"] == compare_ns
        assert compared["whole_spacecraft_restart_parity_proven"] is False
        assert compared["automatic_resume_allowed"] is False
        assert compared["automatic_tc_replay_allowed"] is False
        assert compared["resume_supported"] is False
        assert compared["conclusion"] == "OBSERVED_POST_IMPORT_DIVERGENCE"
        assert "eps.battery_energy_j" in compared["above_threshold_signals"]
        print("R9F_REAL_NATIVE_DIVERGENCE_REPORT:" +
              json.dumps(compared, sort_keys=True), flush=True)

        # Even after the child experiment, the source remains independently
        # live and restart-gated.
        runtime.advance_to(1.2)
        later = runtime.audit_native_checkpoint_coverage()
        assert later["native_joint_owner_observations"]["capture_time_ns"] == 1_200_000_000
        assert build_recovery_readiness(later)["restart_readiness"] == "BLOCKED"
        runtime.finalize()
    finally:
        if runtime._thread is not None and runtime._thread.is_alive():
            runtime.abort()


def test_r9f_guard_unsupported_equivalence_and_rehashed_forgery():
    before = {
        "time_ns": 1_000_000_000,
        "vectors": {
            "hub_position_m": [7e6, 0., 0.],
            "hub_velocity_m_s": [0., 7400., 0.],
            "hub_mrp": [0.1, 0., 0.],
            "hub_angular_rate_rad_s": [0.01, 0., 0.],
            "reaction_wheel_speed_rad_s": [100., 200., 300.],
        },
        "eps": {"battery_energy_j": 50.},
        "thermal": {"payload_temp_k": 300.},
        "comm": {"storage_bits": 100.},
    }
    after = copy.deepcopy(before)
    after["eps"]["battery_energy_j"] = 51.
    report = build_divergence_report(
        uninterrupted=before, imported_child=after,
        source_pid=123, child_pid=456, checkpoint_ns=800_000_000,
    )
    verify_divergence_report(report)
    assert report["conclusion"] == "OBSERVED_POST_IMPORT_DIVERGENCE"
    with pytest.raises(ValueError, match="R9F_SEPARATE_NATIVE_PROCESS_REQUIRED"):
        build_divergence_report(
            uninterrupted=before, imported_child=after,
            source_pid=123, child_pid=123, checkpoint_ns=800_000_000,
        )
    forged = copy.deepcopy(report)
    forged["whole_spacecraft_restart_parity_proven"] = True
    forged["sha256"] = _digest({
        k: v for k, v in forged.items() if k != "sha256"
    })
    with pytest.raises(ValueError, match="R9F_FALSE_RESUME_CLAIM"):
        verify_divergence_report(forged)
    forged = copy.deepcopy(report)
    forged["above_threshold_signals"] = []
    forged["sha256"] = _digest({
        k: v for k, v in forged.items() if k != "sha256"
    })
    with pytest.raises(ValueError, match="R9F_REPORT_CONCLUSION_FORGED"):
        verify_divergence_report(forged)
    # A coincidental one-step numerical match cannot authorize restart.
    exact = build_divergence_report(
        uninterrupted=before, imported_child=before,
        source_pid=123, child_pid=456, checkpoint_ns=800_000_000,
    )
    verify_divergence_report(exact)
    assert exact["conclusion"] == "SAMPLED_SIGNALS_INCONCLUSIVE_NOT_RESUME_PROOF"
    assert exact["whole_spacecraft_restart_parity_proven"] is False


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--r9f-worker":
        _worker(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit("unsupported lab-only native comparison profile")
