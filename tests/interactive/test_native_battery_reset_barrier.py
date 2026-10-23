"""R9-I true Basilisk protected SimpleBattery state API negative control.

Same 0.8s source, both fresh child PIDs use R9-H RW C++ and real applied
torque sample. Experimental child ONLY uses public storedCharge_Init+Reset,
which does not restore protected previousTime/integration/message memory.
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
from sat_sim.interactive.native_battery_reset_comparison import (
    BASE, RESET, _digest, build_battery_reset_barrier_report,
    validate_battery_reset_barrier_report,
)
from test_native_whole_rw_ablation import _read_worker
from test_native_whole_first_tick_diagnostics import _measurement


@pytest.mark.parametrize("topology,count",[
    ("orthogonal_3",3),("pyramid_4",4),
])
def test_r9i_genuine_basilisk_battery_public_reset_must_not_be_called_recovery(
    tmp_path, topology, count,
):
    pytest.importorskip("Basilisk")
    from sat_sim.bsk_engine.unified_native import UnifiedRuntimeConfig
    from sat_sim.interactive.unified_runtime import UnifiedPersistentRuntime
    from sat_sim.interactive.native_checkpoint_audit import verify_coverage_report
    from sat_sim.interactive.native_recovery_readiness import build_recovery_readiness

    origin=UnifiedPersistentRuntime(UnifiedRuntimeConfig(
        capability_id="whole_spacecraft.unified_native.v1",
        duration_s=0.5,step_s=0.2,sample_s=0.2,adcs_only=False,
        values={"rw_configuration":topology},events=(),
        interactive_unbounded=True,
    ),continuous=True)
    origin.prepare()
    try:
        origin.advance_to(0.8)
        audit=origin.audit_native_checkpoint_coverage()
        verify_coverage_report(audit)
        assert build_recovery_readiness(audit)["restart_readiness"]=="BLOCKED"
        assert audit["native_adcs_owner_observations"]["rw_count"]==count
        stored=audit["native_joint_owner_observations"]["eps"]["battery_energy_j"]
        capacity=audit["native_joint_owner_observations"]["eps"]["battery_capacity_j"]
        assert 0<stored<capacity

        stops=[1_000_000_000,1_200_000_000,1_400_000_000,1_600_000_000]
        truth=[]
        for ns in stops:
            origin.advance_to(ns*1e-9)
            record=origin.audit_native_checkpoint_coverage()
            verify_coverage_report(record)
            truth.append(_measurement(
                ns,record["native_state_subset_pilot"]["vectors"],
                record["native_joint_owner_observations"],
            ))
        arms={}
        for mode in (BASE,RESET):
            file=tmp_path / f"r9i-{topology}-{mode}.json"
            file.write_text(json.dumps({
                "source_pid":os.getpid(),
                "checkpoint":audit,
                "comparison_ns":stops[0],
                "comparison_times_ns":stops,
                "ablation_mode":mode,
            },sort_keys=True,allow_nan=False),encoding="utf-8")
            result=_read_worker(file,topology)
            assert result["source_pid"]==os.getpid()
            assert result["child_pid"]!=os.getpid()
            assert result["whole_resume_supported"] is False
            assert result["automatic_tc_replay_allowed"] is False
            assert result["writeback"]["message_history_restored"] is False
            assert [r["time_ns"] for r in result["measurements"]]==stops
            if mode==RESET:
                negative=result["battery_reset_control"]
                assert negative["battery_full_state_restored"] is False
                assert negative["native_previous_time_rehydrated"] is False
                assert negative["native_power_message_histories_restored"] is False
                assert negative["actual_protected_stored_charge_readback"] is None
                assert negative["native_public_initial_charge_j"]==pytest.approx(stored)
                assert negative["source_time_ns"]==800_000_000
            else:
                assert result["battery_reset_control"] is None
            arms[mode]={
                "pid":result["child_pid"],
                "reports":[build_divergence_report(
                    uninterrupted=ref,imported_child=actual,
                    source_pid=os.getpid(),child_pid=result["child_pid"],
                    checkpoint_ns=800_000_000,
                ) for ref,actual in zip(truth,result["measurements"])],
            }
        outcome=build_battery_reset_barrier_report(
            baseline=arms[BASE]["reports"],reset_seed=arms[RESET]["reports"],
            source_pid=os.getpid(),baseline_pid=arms[BASE]["pid"],
            reset_pid=arms[RESET]["pid"],
        )
        validate_battery_reset_barrier_report(outcome)
        assert outcome["native_previous_time_restored"] is False
        assert outcome["whole_spacecraft_parity_proven"] is False
        assert outcome["automatic_resume_allowed"] is False
        assert outcome["automatic_tc_replay_allowed"] is False
        assert len(outcome["samples"])==4
        # The first native step must demonstrate an actual non-inert
        # difference between source initial battery vs source checkpoint
        # energy; no fabricated telemetry or all-zero synthetic outcome.
        assert outcome["samples"][0]["reset_changed"],outcome
        print("R9I_REAL_NATIVE_PUBLIC_BATTERY_RESET_BARRIER:"+json.dumps(
            outcome,sort_keys=True,allow_nan=False,
        ),flush=True)
        last=build_recovery_readiness(origin.audit_native_checkpoint_coverage())
        assert last["restorable_owner_count"]==0
        assert last["automatic_resume_allowed"] is False
        origin.finalize()
    finally:
        if origin._thread is not None and origin._thread.is_alive():
            origin.abort()


def test_r9i_rejects_rehashed_native_battery_recovery_claim():
    from sat_sim.interactive.native_whole_first_tick_diagnostics import (
        build_divergence_report,
    )
    def data(t,energy):
        return {
            "time_ns":t,"vectors":{
                "hub_position_m":[7e6,0.,0.],
                "hub_velocity_m_s":[0.,7000.,0.],
                "hub_mrp":[0.01,0.,0.],
                "hub_angular_rate_rad_s":[0.02,0.,0.],
                "reaction_wheel_speed_rad_s":[100.,200.,300.],
            },"eps":{"battery_energy_j":energy},
            "thermal":{"payload_temp_k":290.},
            "comm":{"storage_bits":1000.},
        }
    bs,ex=[],[]
    for i in range(4):
        t=1_000_000_000+200_000_000*i
        bs.append(build_divergence_report(
            uninterrupted=data(t,100),imported_child=data(t,80),
            source_pid=11,child_pid=22,checkpoint_ns=800_000_000,
        ))
        ex.append(build_divergence_report(
            uninterrupted=data(t,100),imported_child=data(t,95),
            source_pid=11,child_pid=33,checkpoint_ns=800_000_000,
        ))
    record=build_battery_reset_barrier_report(
        baseline=bs,reset_seed=ex,
        source_pid=11,baseline_pid=22,reset_pid=33,
    )
    validate_battery_reset_barrier_report(record)
    forged=copy.deepcopy(record)
    forged["cpp_protected_state_readback_verified"]=True
    forged["sha256"]=_digest({k:v for k,v in forged.items() if k!="sha256"})
    with pytest.raises(ValueError,match="R9I_FALSE_RESTORE_CLAIM"):
        validate_battery_reset_barrier_report(forged)
    forged=copy.deepcopy(record)
    forged["samples"][0]["reset_improved"]=False
    forged["sha256"]=_digest({k:v for k,v in forged.items() if k!="sha256"})
    with pytest.raises(ValueError,match="R9I_SAMPLE_ERROR_FORGED"):
        validate_battery_reset_barrier_report(forged)
    with pytest.raises(ValueError,match="R9I_UNPAIRED_NATIVE_OWNER"):
        build_battery_reset_barrier_report(
            baseline=bs,reset_seed=ex,
            source_pid=11,baseline_pid=22,reset_pid=44,
        )
