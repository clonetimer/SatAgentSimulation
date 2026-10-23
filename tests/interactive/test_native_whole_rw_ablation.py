"""R9-G: isolate native RW C++ memory in a real 3/4RW whole-graph first tick."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from sat_sim.interactive.native_whole_first_tick_diagnostics import (
    build_divergence_report,
)
from sat_sim.interactive.native_whole_rw_ablation import (
    BASIC, RW_MEMORY, create_ablation_record, validate_ablation_record,
)


def _read_worker(path: Path, topology: str):
    child = subprocess.run(
        [sys.executable,
         str(Path(__file__).with_name("test_native_whole_first_tick_diagnostics.py")),
         "--r9f-worker", str(path), topology],
        capture_output=True, text=True, timeout=70, check=False,
    )
    assert child.returncode == 0, child.stdout + "\n" + child.stderr
    lines = [
        line.removeprefix("R9F_POST_IMPORT_JSON:")
        for line in child.stdout.splitlines()
        if line.startswith("R9F_POST_IMPORT_JSON:")
    ]
    assert len(lines) == 1, child.stdout
    return json.loads(lines[0])


@pytest.mark.parametrize("topology,count", [("orthogonal_3", 3), ("pyramid_4", 4)])
def test_r9g_full_basilisk_rw_cpp_memory_isolated_ablation(
    tmp_path, topology, count,
):
    pytest.importorskip("Basilisk")
    from sat_sim.bsk_engine.unified_native import UnifiedRuntimeConfig
    from sat_sim.interactive.unified_runtime import UnifiedPersistentRuntime
    from sat_sim.interactive.native_checkpoint_audit import verify_coverage_report
    from sat_sim.interactive.native_recovery_readiness import build_recovery_readiness

    origin = UnifiedPersistentRuntime(UnifiedRuntimeConfig(
        capability_id="whole_spacecraft.unified_native.v1",
        duration_s=0.5, step_s=0.2, sample_s=0.2, adcs_only=False,
        values={"rw_configuration": topology}, events=(),
        interactive_unbounded=True,
    ), continuous=True)
    origin.prepare()
    try:
        origin.advance_to(0.8)
        audit = origin.audit_native_checkpoint_coverage()
        verify_coverage_report(audit)
        assert build_recovery_readiness(audit)["restart_readiness"] == "BLOCKED"
        assert audit["native_adcs_owner_observations"]["rw_count"] == count
        assert len(audit["native_adcs_owner_observations"]["native_rw_cpp_memory"]) == count

        # One common uninterrupted physical baseline for both child arms.
        origin.advance_to(1.0)
        source_now = origin.audit_native_checkpoint_coverage()
        verify_coverage_report(source_now)
        from test_native_whole_first_tick_diagnostics import _measurement
        source = _measurement(
            1_000_000_000, source_now["native_state_subset_pilot"]["vectors"],
            source_now["native_joint_owner_observations"],
        )

        reports = {}
        for mode in (BASIC, RW_MEMORY):
            file = tmp_path / f"{topology}-{mode}.json"
            file.write_text(json.dumps({
                "source_pid": os.getpid(),
                "checkpoint": audit,
                "comparison_ns": 1_000_000_000,
                "ablation_mode": mode,
            }, sort_keys=True, allow_nan=False), encoding="utf-8")
            output = _read_worker(file, topology)
            assert output["ablation_mode"] == mode
            assert output["source_pid"] == os.getpid()
            assert output["child_pid"] != os.getpid()
            assert output["whole_resume_supported"] is False
            assert output["automatic_tc_replay_allowed"] is False
            reports[mode] = build_divergence_report(
                uninterrupted=source, imported_child=output["measurement"],
                source_pid=os.getpid(), child_pid=output["child_pid"],
                checkpoint_ns=800_000_000,
            )

        result = create_ablation_record(
            baseline=reports[BASIC], with_rw_memory=reports[RW_MEMORY],
            source_pid=os.getpid(),
            baseline_child_pid=reports[BASIC]["child_pid"],
            rw_memory_child_pid=reports[RW_MEMORY]["child_pid"],
        )
        validate_ablation_record(result)
        assert result["whole_graph_parity_proven"] is False
        assert result["whole_spacecraft_resume_supported"] is False
        assert result["automatic_tc_replay_allowed"] is False
        assert result["owner_closure_proven"] is False

        # A test cannot just 'pass' for an inert RW restoration hook. This
        # controls for the actual native reaction-wheel C++ effect on dynamics.
        affected = {
            item["owner_signal"] for item in result["signal_deltas"]
            if item["changed"]
        }
        assert "adcs.reaction_wheel_speed_rad_s" in affected, result
        print("R9G_REAL_NATIVE_RW_ABLATION:" +
              json.dumps(result, sort_keys=True, allow_nan=False), flush=True)

        origin.advance_to(1.2)
        gate = build_recovery_readiness(origin.audit_native_checkpoint_coverage())
        assert gate["automatic_resume_allowed"] is False
        assert gate["restorable_owner_count"] == 0
        origin.finalize()
    finally:
        if origin._thread is not None and origin._thread.is_alive():
            origin.abort()


def test_r9g_refuses_rehashed_recovery_claim_or_fake_effect():
    from sat_sim.interactive.native_whole_first_tick_diagnostics import _digest
    from sat_sim.interactive.native_whole_rw_ablation import _hash
    import hashlib

    def measurement(energy):
        return {
            "time_ns": 1_000_000_000,
            "vectors": {
                "hub_position_m": [7e6,0.,0.],
                "hub_velocity_m_s": [0.,7_400.,0.],
                "hub_mrp": [0.01,0.,0.],
                "hub_angular_rate_rad_s": [0.005,0.,0.],
                "reaction_wheel_speed_rad_s": [100.,200.,300.],
            },
            "eps": {"battery_energy_j": energy},
            "thermal": {"payload_temp_k": 293.},
            "comm": {"storage_bits": 1000.},
        }
    base = build_divergence_report(
        uninterrupted=measurement(100), imported_child=measurement(80),
        source_pid=111, child_pid=222, checkpoint_ns=800_000_000,
    )
    rw = build_divergence_report(
        uninterrupted=measurement(100), imported_child=measurement(90),
        source_pid=111, child_pid=333, checkpoint_ns=800_000_000,
    )
    result = create_ablation_record(
        baseline=base, with_rw_memory=rw,
        source_pid=111, baseline_child_pid=222, rw_memory_child_pid=333,
    )
    assert any(item["improved"] for item in result["signal_deltas"])
    forged = copy.deepcopy(result)
    forged["whole_spacecraft_resume_supported"] = True
    forged["sha256"] = _hash({k:v for k,v in forged.items() if k != "sha256"})
    with pytest.raises(ValueError, match="R9G_ABLATION_FALSE_RESTORE_CLAIM"):
        validate_ablation_record(forged)
    forged = copy.deepcopy(result)
    forged["signal_deltas"][5]["improved"] = False
    forged["sha256"] = _hash({k:v for k,v in forged.items() if k != "sha256"})
    with pytest.raises(ValueError, match="R9G_ABLATION_DERIVED_RESULT_FORGED"):
        validate_ablation_record(forged)
    # A child PID that contradicts the signed child measurement is
    # rejected as a non-paired experiment before process-uniqueness checks.
    with pytest.raises(ValueError, match="R9G_ABLATION_COMPARISON_NOT_PAIRED"):
        create_ablation_record(
            baseline=base, with_rw_memory=rw,
            source_pid=111, baseline_child_pid=222, rw_memory_child_pid=222,
        )
