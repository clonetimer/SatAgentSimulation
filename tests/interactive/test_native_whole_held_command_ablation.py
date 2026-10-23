"""R9-H real unified Basilisk 3/4RW multi-step applied-command lab.

Both children import identical R9-G real C++ RW memory. A single additional
post-fault-manager applied motor message is the only variable. Genuine native
Basilisk processes execute 4 steps and compare against one live original.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from sat_sim.interactive.native_whole_first_tick_diagnostics import (
    build_divergence_report,
)
from sat_sim.interactive.native_whole_held_command_ablation import (
    BASELINE, HELD_COMMAND, make_multistep_ablation,
    validate_multistep_ablation, _digest,
)
from test_native_whole_rw_ablation import _read_worker
from test_native_whole_first_tick_diagnostics import _measurement


@pytest.mark.parametrize("topology,count", [
    ("orthogonal_3",3),("pyramid_4",4),
])
def test_r9h_real_basilisk_four_native_ticks_applied_command_ablation(
    tmp_path,topology,count,
):
    pytest.importorskip("Basilisk")
    from sat_sim.bsk_engine.unified_native import UnifiedRuntimeConfig
    from sat_sim.interactive.unified_runtime import UnifiedPersistentRuntime
    from sat_sim.interactive.native_checkpoint_audit import verify_coverage_report
    from sat_sim.interactive.native_recovery_readiness import build_recovery_readiness

    original=UnifiedPersistentRuntime(UnifiedRuntimeConfig(
        capability_id="whole_spacecraft.unified_native.v1",
        duration_s=0.5,step_s=0.2,sample_s=0.2,adcs_only=False,
        values={"rw_configuration":topology},events=(),
        interactive_unbounded=True,
    ),continuous=True)
    original.prepare()
    try:
        original.advance_to(0.8)
        checkpoint=original.audit_native_checkpoint_coverage()
        verify_coverage_report(checkpoint)
        owner=checkpoint["native_adcs_owner_observations"]
        assert owner["rw_count"]==count
        applied=owner.get("native_applied_motor_command_nm")
        assert isinstance(applied,list) and len(applied)==count
        assert all(abs(x)<=0.20000001 for x in applied)
        assert build_recovery_readiness(checkpoint)["restart_readiness"]=="BLOCKED"

        stops=[1_000_000_000,1_200_000_000,1_400_000_000,1_600_000_000]
        truth=[]
        for ns in stops:
            original.advance_to(ns*1e-9)
            seen=original.audit_native_checkpoint_coverage()
            verify_coverage_report(seen)
            assert seen["native_scheduler_phase"]["current_nanos"]==ns
            truth.append(_measurement(
                ns,seen["native_state_subset_pilot"]["vectors"],
                seen["native_joint_owner_observations"],
            ))
        arms={}
        for mode in (BASELINE,HELD_COMMAND):
            path=tmp_path / f"r9h-{topology}-{mode}.json"
            path.write_text(json.dumps({
                "source_pid":os.getpid(),
                "checkpoint":checkpoint,
                "comparison_ns":stops[0],
                "comparison_times_ns":stops,
                "ablation_mode":mode,
            },sort_keys=True,allow_nan=False),encoding="utf-8")
            result=_read_worker(path,topology)
            assert result["ablation_mode"]==mode
            assert result["source_pid"]==os.getpid()
            assert result["child_pid"]!=os.getpid()
            assert [x["time_ns"] for x in result["measurements"]]==stops
            assert result["whole_resume_supported"] is False
            assert result["automatic_tc_replay_allowed"] is False
            if mode==HELD_COMMAND:
                writeback=result["writeback"]
                assert writeback["native_applied_command_nm"]==applied
                assert writeback["single_message_writeback"] is True
                assert writeback["message_history_restored"] is False
                assert writeback["whole_spacecraft_parity_proven"] is False
            else:
                assert result["writeback"] is None
            arms[mode]={
                "pid":result["child_pid"],
                "reports":[build_divergence_report(
                    uninterrupted=reference,imported_child=candidate,
                    source_pid=os.getpid(),child_pid=result["child_pid"],
                    checkpoint_ns=800_000_000,
                ) for reference,candidate in zip(truth,result["measurements"])],
            }
        pair=make_multistep_ablation(
            baseline_reports=arms[BASELINE]["reports"],
            command_reports=arms[HELD_COMMAND]["reports"],
            source_pid=os.getpid(),
            baseline_pid=arms[BASELINE]["pid"],
            command_pid=arms[HELD_COMMAND]["pid"],
        )
        validate_multistep_ablation(pair)
        assert pair["whole_spacecraft_physical_parity_proven"] is False
        assert pair["automatic_resume_allowed"] is False
        assert pair["automatic_tc_replay_allowed"] is False
        assert pair["complete_message_history"] is False
        assert pair["baseline_pid"]!=pair["command_pid"]
        assert len(pair["epochs"])==4
        print("R9H_REAL_NATIVE_HELD_COMMAND_MULTISTEP:"+json.dumps(
            pair,sort_keys=True,allow_nan=False,
        ),flush=True)

        gate=build_recovery_readiness(original.audit_native_checkpoint_coverage())
        assert gate["restart_readiness"]=="BLOCKED"
        assert gate["restorable_owner_count"]==0
        assert gate["automatic_resume_allowed"] is False
        original.finalize()
    finally:
        if original._thread is not None and original._thread.is_alive():
            original.abort()


def test_r9h_rehashed_report_cannot_claim_restart_or_fake_improvement():
    from sat_sim.interactive.native_whole_first_tick_diagnostics import (
        build_divergence_report,
    )
    def observation(t,energy):
        return {
            "time_ns":t,
            "vectors":{
                "hub_position_m":[7e6,0.,0.],
                "hub_velocity_m_s":[0.,7000.,0.],
                "hub_mrp":[0.01,0.,0.],
                "hub_angular_rate_rad_s":[0.02,0.,0.],
                "reaction_wheel_speed_rad_s":[100.,200.,300.],
            },
            "eps":{"battery_energy_j":energy},
            "thermal":{"payload_temp_k":290.},
            "comm":{"storage_bits":1200.},
        }
    basic,enhanced=[],[]
    for n in range(1,5):
        t=800_000_000+n*200_000_000
        ref=observation(t,100.)
        basic.append(build_divergence_report(
            uninterrupted=ref,imported_child=observation(t,80.),
            source_pid=111,child_pid=222,checkpoint_ns=800_000_000,
        ))
        enhanced.append(build_divergence_report(
            uninterrupted=ref,imported_child=observation(t,90.),
            source_pid=111,child_pid=333,checkpoint_ns=800_000_000,
        ))
    report=make_multistep_ablation(
        baseline_reports=basic,command_reports=enhanced,
        source_pid=111,baseline_pid=222,command_pid=333,
    )
    validate_multistep_ablation(report)
    forged=copy.deepcopy(report)
    forged["whole_spacecraft_physical_parity_proven"]=True
    forged["sha256"]=_digest({k:v for k,v in forged.items() if k!="sha256"})
    with pytest.raises(ValueError,match="R9H_ABLATION_FALSE_RESUME_CLAIM"):
        validate_multistep_ablation(forged)
    forged=copy.deepcopy(report)
    forged["epochs"][0]["signals"][5]["improved"]=False
    forged["sha256"]=_digest({k:v for k,v in forged.items() if k!="sha256"})
    with pytest.raises(ValueError,match="R9H_ABLATION_IMPROVEMENT_FORGED"):
        validate_multistep_ablation(forged)
    forged=copy.deepcopy(report)
    forged["epochs"][2]["time_ns"]=forged["epochs"][1]["time_ns"]
    forged["sha256"]=_digest({k:v for k,v in forged.items() if k!="sha256"})
    with pytest.raises(ValueError,match="R9H_ABLATION_GRID_INVALID"):
        validate_multistep_ablation(forged)
    with pytest.raises(ValueError,match="R9H_ABLATION_SOURCE_NOT_PAIRED"):
        make_multistep_ablation(
            baseline_reports=basic,command_reports=enhanced,
            source_pid=111,baseline_pid=222,command_pid=444,
        )
