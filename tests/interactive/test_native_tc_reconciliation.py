"""Real Basilisk pending ADCS TC journal / ACK cross-process pilot.

The durable journal MUST mark EXECUTING on disk before the native guidance
route mutation. Unknown in-flight outcomes are never automatically replayed.
This only covers a single isolated future inertial->nadir TC, not the real
interactive session manager's workspace/ACK history recovery.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sat_sim.interactive.native_tc_reconciliation import NativeTcPilotJournal
from sat_sim.interactive.native_mode_checkpoint import (
    capture_mode_checkpoint, validate_mode_checkpoint,
)
from sat_sim.interactive.native_guidance_route_pilot import (
    restore_native_guidance_route_fresh, switch_native_guidance,
)
from sat_sim.interactive.native_closed_loop_evidence import (
    capture_closed_loop_evidence, restore_rw_effector_memory_fresh,
    verify_closed_loop_evidence,
)
from sat_sim.interactive.native_state_pilot import (
    capture_state_subset, restore_state_subset_in_place,
)
from sat_sim.interactive.native_multiprocess_pilot import (
    restore_two_process_clock_fresh,
)
from test_native_closed_loop_checkpoint import _build, _closed_loop_rows

CHECKPOINT_NS = 2_000_000_000
DUE_NS = 2_200_000_000
END_NS = 5_000_000_000
COMMAND_ID = "tc-nadir-once"


def _restore_worker(package_path: str, journal_path: str, mode: str) -> None:
    from Basilisk.architecture import messaging
    if mode not in ("apply_once", "omit_due"):
        raise ValueError("TC_NATIVE_UNSUPPORTED_WORKER_MODE")
    data = json.loads(Path(package_path).read_text(encoding="utf-8"))
    cp = data["evidence"]
    mode_evidence = data["guidance_mode_checkpoint"]
    verify_closed_loop_evidence(cp)
    mode_state = validate_mode_checkpoint(mode_evidence)
    if (mode_state["checkpoint_ns"] != cp["checkpoint_sim_time_ns"]
            or mode_state["current_mode"] != "inertial3d"):
        raise ValueError("TC_NATIVE_MODE_AND_PHYSICS_CHECKPOINT_MISMATCH")
    ledger = NativeTcPilotJournal(Path(journal_path))
    plan = ledger.plan_recovery()
    if (plan["checkpoint_ns"] != CHECKPOINT_NS
            or plan["safe_future"] != [COMMAND_ID]
            or plan["already_acked"]):
        raise ValueError("TC_NATIVE_RECOVERY_PLAN_MISMATCH")

    graph = _build(closed_loop=True)
    restore_native_guidance_route_fresh(graph, mode_evidence)
    restore_state_subset_in_place(
        graph["sc"], cp["spacecraft_rw_state"], expected_rw_count=3,
    )
    restore_rw_effector_memory_fresh(graph["rw"], cp["rw_effector_memory"])
    restore_two_process_clock_fresh(
        graph["sim"], graph["processes"], graph["tasks"],
        graph["sc"], cp["native_scheduler"],
    )
    fsw = cp["fsw_integrator"]
    for name in ("K", "P", "Ki"):
        assert getattr(graph["controller"], name) == fsw[name]
    assert graph["controller"].rwConfigParams.numRW == fsw["cached_rw_count"] == 3
    graph["controller"].priorTime = fsw["prior_time_ns"]
    graph["controller"].int_sigma = fsw["int_sigma"]
    graph["controller"].z = fsw["z"]
    payload = messaging.ArrayMotorTorqueMsgPayload()
    payload.motorTorque = data["last_rw_motor_payload"]
    graph["allocator"].rwMotorTorqueOutMsg.write(payload, fsw["prior_time_ns"])

    graph["sim"].ConfigureStopTime(DUE_NS)
    graph["sim"].ExecuteSimulation()
    if mode == "apply_once":
        claimed = ledger.claim_due(command_id=COMMAND_ID, now_ns=DUE_NS)
        assert claimed["target_mode"] == "nadir" and claimed["state"] == "EXECUTING"
        switch_native_guidance(graph, "nadir")
        # The applied journal record is written only *after* native mutation.
        ledger.acknowledge_applied(command_id=COMMAND_ID, at_ns=DUE_NS)
        try:
            ledger.claim_due(command_id=COMMAND_ID, now_ns=DUE_NS)
        except ValueError as exc:
            assert "ALREADY_ACKED_NO_REPLAY" in str(exc)
        else:
            raise AssertionError("ACKED TC accidentally re-dispatched")

    graph["sim"].ConfigureStopTime(END_NS)
    graph["sim"].ExecuteSimulation()
    print("NATIVE_TC_RECONCILE_JSON:" + json.dumps({
        "pid": os.getpid(), "end_ns": int(graph["sim"].TotalSim.CurrentNanos),
        "resume_supported": False, "command_state": ledger.read()["commands"][0]["state"],
        "telemetry": _closed_loop_rows(graph, after_ns=CHECKPOINT_NS),
        "final_state": capture_state_subset(graph["sc"], expected_rw_count=3),
    }, sort_keys=True), flush=True)


def test_future_native_mode_tc_ack_journal_cross_process_parity(tmp_path) -> None:
    pytest.importorskip("Basilisk")
    baseline = _build(closed_loop=True)
    baseline["sim"].ConfigureStopTime(CHECKPOINT_NS)
    baseline["sim"].ExecuteSimulation()
    cp = capture_closed_loop_evidence(
        sim=baseline["sim"], processes=baseline["processes"],
        tasks=baseline["tasks"], spacecraft=baseline["sc"],
        controller=baseline["controller"], allocator=baseline["allocator"],
        rw_effector=baseline["rw"],
    )
    journal = NativeTcPilotJournal(tmp_path / "source-tc.json")
    journal.create(session_id="adcs-tc-pilot", checkpoint_ns=CHECKPOINT_NS)
    journal.enqueue(command_id=COMMAND_ID, target_mode="nadir", execute_at_ns=DUE_NS)
    assert journal.plan_recovery()["safe_future"] == [COMMAND_ID]
    mode_evidence = capture_mode_checkpoint(
        checkpoint_ns=CHECKPOINT_NS, current_mode="inertial3d",
        transitions=[{
            "event_id": "initial-mode", "revision": 1, "requested_ns": 0,
            "resolved_ns": 0, "from_mode": "standby",
            "to_mode": "inertial3d", "status": "applied",
        }],
    )
    package = tmp_path / "tc-physical-checkpoint.json"
    package.write_text(json.dumps({
        "evidence": cp, "guidance_mode_checkpoint": mode_evidence,
        "last_rw_motor_payload": [
            float(v) for v in baseline["allocator"].rwMotorTorqueOutMsg.read().motorTorque
        ],
    }, sort_keys=True), encoding="utf-8")

    # Clone the exact on-disk accepted-command snapshot BEFORE the source
    # dispatches/ACKs. Each fresh process owns an independent copy.
    copies = {}
    for name in ("apply_once", "omit_due"):
        destination = tmp_path / (name + "-tc.json")
        shutil.copyfile(journal.path, destination)
        copies[name] = destination

    baseline["sim"].ConfigureStopTime(DUE_NS)
    baseline["sim"].ExecuteSimulation()
    assert journal.claim_due(command_id=COMMAND_ID, now_ns=DUE_NS)["state"] == "EXECUTING"
    switch_native_guidance(baseline, "nadir")
    journal.acknowledge_applied(command_id=COMMAND_ID, at_ns=DUE_NS)
    baseline["sim"].ConfigureStopTime(END_NS)
    baseline["sim"].ExecuteSimulation()
    expected = _closed_loop_rows(baseline, after_ns=CHECKPOINT_NS)
    final = capture_state_subset(baseline["sc"], expected_rw_count=3)

    results = {}
    for name, path in copies.items():
        proc = subprocess.run([
            sys.executable, str(Path(__file__).resolve()), "--native-tc-worker",
            str(package), str(path), name,
        ], capture_output=True, text=True, check=False, timeout=45)
        assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
        report = json.loads(next(
            row.removeprefix("NATIVE_TC_RECONCILE_JSON:")
            for row in proc.stdout.splitlines()
            if row.startswith("NATIVE_TC_RECONCILE_JSON:")
        ))
        assert report["pid"] != os.getpid()
        assert report["end_ns"] == END_NS
        assert report["resume_supported"] is False
        results[name] = report

    assert results["apply_once"]["command_state"] == "ACKED"
    assert results["omit_due"]["command_state"] == "QUEUED"
    for name in ("attitude_mrp", "body_rate", "fsw_torque",
                 "motor_torque", "rw_speeds"):
        rows, expected_rows = results["apply_once"]["telemetry"][name], expected[name]
        assert [t for t, _ in rows] == [t for t, _ in expected_rows], name
        assert len(rows) >= 3
        for idx, ((t, actual), (_, reference)) in enumerate(zip(rows, expected_rows)):
            assert actual == pytest.approx(reference, abs=1e-8, rel=1e-11), (
                f"TC_RECONCILE_FIRST_MISMATCH channel={name} index={idx} time_ns={t}"
            )
    for key, values in final["vectors"].items():
        assert results["apply_once"]["final_state"]["vectors"][key] == pytest.approx(
            values, abs=1e-8, rel=1e-11,
        ), key
    wrong = results["omit_due"]
    delta = max(
        abs(a - b)
        for (_, actual), (_, reference) in zip(
            wrong["telemetry"]["fsw_torque"], expected["fsw_torque"]
        )
        for a, b in zip(actual, reference)
    )
    assert delta > 1e-7, "Omitted durable future TC did not change native control torque"


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--native-tc-worker":
        _restore_worker(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        raise SystemExit("native TC reconciliation test worker only")
