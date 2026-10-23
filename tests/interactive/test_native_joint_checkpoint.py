"""R5: real Basilisk simultaneous mode / RW fault / durable TC recovery.

A single t=2.0s composite checkpoint captures active native RW command
dropout, inertial FSW guidance with pending nadir TC, 3RW C++ internal
memory, C FSW integrator, and the exact two-process scheduler phase.
Baseline and distinct Python OS process both execute TC at 2.2s while
fault persists until 3.2s, then continue to absolute t=5.0s.

This is deliberately a fixed, isolated test graph and NOT production resume.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sat_sim.interactive.native_closed_loop_evidence import (
    capture_closed_loop_evidence, restore_rw_effector_memory_fresh,
)
from sat_sim.interactive.native_guidance_route_pilot import (
    restore_native_guidance_route_fresh, switch_native_guidance,
)
from sat_sim.interactive.native_joint_checkpoint import (
    CP_NS, FAULT_START_NS, FAULT_END_NS, TC_DUE_NS,
    capture_joint_checkpoint, verify_joint_checkpoint, _digest,
)
from sat_sim.interactive.native_mode_checkpoint import capture_mode_checkpoint
from sat_sim.interactive.native_rw_fault_checkpoint import (
    apply_native_motor_dropout, capture_fault_checkpoint,
    restore_native_fault_route_fresh,
)
from sat_sim.interactive.native_tc_reconciliation import NativeTcPilotJournal
from sat_sim.interactive.native_multiprocess_pilot import restore_two_process_clock_fresh
from sat_sim.interactive.native_state_pilot import (
    capture_state_subset, restore_state_subset_in_place,
)
from test_native_closed_loop_checkpoint import _build, _closed_loop_rows

SESSION = "native-joint-adcs-r5"
COMMAND_ID = "tc-nadir-after-fault"
END_NS = 5_000_000_000


def _make_joint_source(tmp_path):
    graph = _build(closed_loop=True)
    journal = NativeTcPilotJournal(tmp_path / "source-tc-journal.json")
    journal.create(session_id=SESSION, checkpoint_ns=CP_NS)
    assert journal.enqueue(
        command_id=COMMAND_ID, target_mode="nadir", execute_at_ns=TC_DUE_NS,
    ) == "QUEUED"

    # Activate an actual Basilisk reaction wheel input command interruption
    # immediately following the slow task at 1.8 s.
    graph["sim"].ConfigureStopTime(FAULT_START_NS)
    graph["sim"].ExecuteSimulation()
    apply_native_motor_dropout(graph, active=True)
    graph["sim"].ConfigureStopTime(CP_NS)
    graph["sim"].ExecuteSimulation()

    physics = capture_closed_loop_evidence(
        sim=graph["sim"], processes=graph["processes"],
        tasks=graph["tasks"], spacecraft=graph["sc"],
        controller=graph["controller"], allocator=graph["allocator"],
        rw_effector=graph["rw"],
    )
    mode = capture_mode_checkpoint(
        checkpoint_ns=CP_NS, current_mode="inertial3d",
        transitions=[
            {
                "event_id": "startup-inertial", "revision": 1,
                "requested_ns": 0, "resolved_ns": 0,
                "from_mode": "standby", "to_mode": "inertial3d",
                "status": "applied",
            },
            {
                "event_id": COMMAND_ID, "revision": 2,
                "requested_ns": FAULT_START_NS, "resolved_ns": None,
                "from_mode": "inertial3d", "to_mode": "nadir",
                "status": "pending",
            },
        ],
    )
    fault = capture_fault_checkpoint(
        fault_id="rw-command-dropout-r5", checkpoint_ns=CP_NS,
        start_ns=FAULT_START_NS, end_ns=FAULT_END_NS,
    )
    joint = capture_joint_checkpoint(
        session_id=SESSION, physics=physics, mode=mode,
        fault=fault, tc=journal.read(),
    )
    assert joint["resume_supported"] is False
    assert graph["rw"].rwMotorCmdInMsg.isLinked()
    return graph, journal, joint


def _continue(graph, ledger: NativeTcPilotJournal, *, omit: str = "none"):
    graph["sim"].ConfigureStopTime(TC_DUE_NS)
    graph["sim"].ExecuteSimulation()
    if omit != "omit_tc":
        claim = ledger.claim_due(command_id=COMMAND_ID, now_ns=TC_DUE_NS)
        assert claim["target_mode"] == "nadir"
        assert claim["state"] == "EXECUTING"
        switch_native_guidance(graph, "nadir")
        ledger.acknowledge_applied(command_id=COMMAND_ID, at_ns=TC_DUE_NS)

    graph["sim"].ConfigureStopTime(FAULT_END_NS)
    graph["sim"].ExecuteSimulation()
    if omit != "omit_clear":
        apply_native_motor_dropout(graph, active=False)
    graph["sim"].ConfigureStopTime(END_NS)
    graph["sim"].ExecuteSimulation()
    return {
        "end_ns": int(graph["sim"].TotalSim.CurrentNanos),
        "tc_state": ledger.read()["commands"][0]["state"],
        "final_state": capture_state_subset(graph["sc"], expected_rw_count=3),
        "telemetry": _closed_loop_rows(graph, after_ns=CP_NS),
    }


def _worker(package_path: str, journal_path: str, omission: str):
    from Basilisk.architecture import messaging

    if omission not in ("none", "omit_fault_restore", "omit_tc", "omit_clear"):
        raise ValueError("JOINT_NATIVE_WORKER_PROFILE_INVALID")
    wrapper = json.loads(Path(package_path).read_text(encoding="utf-8"))
    composite = wrapper["joint"]
    validated = verify_joint_checkpoint(composite)
    ledger = NativeTcPilotJournal(Path(journal_path))
    if ledger.read() != composite["owners"]["tc"]:
        raise ValueError("JOINT_NATIVE_TC_LEDGER_NOT_SAME_SNAPSHOT")
    expected_plan = ledger.plan_recovery()
    if (expected_plan["session_id"] != SESSION
            or expected_plan["safe_future"] != [COMMAND_ID]
            or expected_plan["already_acked"]):
        raise ValueError("JOINT_NATIVE_UNSAFE_REPLAY_PLAN")

    physics = validated["owners"]["physics"]
    mode = validated["owners"]["mode"]
    fault = validated["owners"]["fault"]
    graph = _build(closed_loop=True)
    restore_native_guidance_route_fresh(graph, mode)
    if omission != "omit_fault_restore":
        restore_native_fault_route_fresh(graph, fault)
    restore_state_subset_in_place(
        graph["sc"], physics["spacecraft_rw_state"], expected_rw_count=3,
    )
    restore_rw_effector_memory_fresh(graph["rw"], physics["rw_effector_memory"])
    restore_two_process_clock_fresh(
        graph["sim"], graph["processes"], graph["tasks"],
        graph["sc"], physics["native_scheduler"],
    )
    fsw = physics["fsw_integrator"]
    assert graph["controller"].rwConfigParams.numRW == fsw["cached_rw_count"] == 3
    for scalar in ("K", "P", "Ki"):
        assert float(getattr(graph["controller"], scalar)) == fsw[scalar]
    graph["controller"].priorTime = fsw["prior_time_ns"]
    graph["controller"].int_sigma = fsw["int_sigma"]
    graph["controller"].z = fsw["z"]

    payload = messaging.ArrayMotorTorqueMsgPayload()
    payload.motorTorque = wrapper["last_rw_motor_payload"]
    graph["allocator"].rwMotorTorqueOutMsg.write(payload, fsw["prior_time_ns"])
    result = _continue(graph, ledger, omit="none" if omission == "omit_fault_restore" else omission)
    result.update({
        "pid": os.getpid(), "experimental_only": True,
        "resume_supported": False, "omission": omission,
    })
    print("JOINT_ADCS_RESTART_JSON:" + json.dumps(result, sort_keys=True), flush=True)


@pytest.mark.parametrize("omit", ["omit_fault_restore", "omit_tc", "omit_clear"])
def test_joint_native_cross_process_with_adverse_controls(tmp_path, omit):
    pytest.importorskip("Basilisk")
    graph, journal, joint = _make_joint_source(tmp_path)
    path = tmp_path / "r5-composite-checkpoint.json"
    path.write_text(json.dumps({
        "joint": joint,
        "last_rw_motor_payload": [
            float(x) for x in graph["allocator"].rwMotorTorqueOutMsg.read().motorTorque
        ],
    }, sort_keys=True), encoding="utf-8")

    # All independent OS-process experiments start from the exact same durable
    # bytes, before the baseline claims or ACKs its pending command.
    locations = {}
    for name in ("none", omit):
        location = tmp_path / (name + ".tc.json")
        shutil.copyfile(journal.path, location)
        locations[name] = location

    baseline = _continue(graph, journal)
    assert baseline["tc_state"] == "ACKED"
    assert baseline["end_ns"] == END_NS
    outputs = {}
    for name, ledger_path in locations.items():
        run = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()),
             "--joint-worker", str(path), str(ledger_path), name],
            capture_output=True, text=True, timeout=45, check=False,
        )
        assert run.returncode == 0, run.stdout + "\n" + run.stderr
        report = json.loads(next(
            line.removeprefix("JOINT_ADCS_RESTART_JSON:")
            for line in run.stdout.splitlines()
            if line.startswith("JOINT_ADCS_RESTART_JSON:")
        ))
        assert report["pid"] != os.getpid()
        assert report["resume_supported"] is False
        assert report["end_ns"] == END_NS
        outputs[name] = report

    correct = outputs["none"]
    assert correct["tc_state"] == "ACKED"
    assert outputs[omit]["tc_state"] == (
        "QUEUED" if omit == "omit_tc" else "ACKED"
    )
    for channel in (
        "attitude_mrp", "body_rate", "fsw_torque", "motor_torque", "rw_speeds",
    ):
        observed, reference = correct["telemetry"][channel], baseline["telemetry"][channel]
        assert [t for t, _ in observed] == [t for t, _ in reference], channel
        assert len(reference) >= 3
        for index, ((t, values), (_, expected)) in enumerate(zip(observed, reference)):
            assert values == pytest.approx(expected, abs=1e-8, rel=1e-11), (
                f"JOINT_FIRST_NATIVE_MISMATCH channel={channel} row={index} t_ns={t}"
            )
    for name, vec in baseline["final_state"]["vectors"].items():
        assert correct["final_state"]["vectors"][name] == pytest.approx(
            vec, abs=1e-8, rel=1e-11
        ), name

    wrong = outputs[omit]
    # The causal omission must manifest in native FSW output OR true wheel
    # physics (never rely solely on a control-plane journal status mismatch).
    wheels = max(abs(a - b) for a, b in zip(
        wrong["final_state"]["vectors"]["reaction_wheel_speed_rad_s"],
        baseline["final_state"]["vectors"]["reaction_wheel_speed_rad_s"],
    ))
    torques = max(
        abs(a - b)
        for (_, values), (_, ref) in zip(
            wrong["telemetry"]["fsw_torque"], baseline["telemetry"]["fsw_torque"],
        )
        for a, b in zip(values, ref)
    )
    assert wheels > 1e-7 or torques > 1e-7, (omit, wheels, torques)


def test_joint_checkpoint_detects_cross_owner_tampering(tmp_path):
    pytest.importorskip("Basilisk")
    graph, journal, joint = _make_joint_source(tmp_path)
    broken = copy.deepcopy(joint)
    broken["owners"]["tc"]["commands"][0]["target_mode"] = "inertial3d"
    with pytest.raises(ValueError, match="JOINT_CHECKPOINT_DIGEST_INVALID"):
        verify_joint_checkpoint(broken)
    from sat_sim.interactive.native_tc_reconciliation import _hash as tc_hash
    # Even a recomputed top-level digest and owner digest cannot conceal a
    # tampered child owner signature.
    broken["owners"]["tc"]["digest_sha256"] = tc_hash({
        k: v for k, v in broken["owners"]["tc"].items() if k != "digest_sha256"
    })
    broken["owner_digests_sha256"]["tc"] = _digest(broken["owners"]["tc"])
    broken["digest_sha256"] = _digest({
        k: v for k, v in broken.items() if k != "digest_sha256"
    })
    with pytest.raises(ValueError, match="JOINT_CHECKPOINT_TC_MODE_CAUSALITY_MISMATCH"):
        verify_joint_checkpoint(broken)


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--joint-worker":
        _worker(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        raise SystemExit("test-only native composite checkpoint worker")
