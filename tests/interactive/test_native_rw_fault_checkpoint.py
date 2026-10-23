"""Real-Basilisk command-dropout fault lifecycle restart parity (isolated).

Two cases: active before 2.0s checkpoint, or scheduled for 2.2s afterward.
The fault disconnects actual 3-wheel actuator input from live FSW output
by routing it to a constant zero command message. No Python physics proxy.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sat_sim.interactive.native_closed_loop_evidence import (
    capture_closed_loop_evidence, restore_rw_effector_memory_fresh,
    verify_closed_loop_evidence,
)
from sat_sim.interactive.native_rw_fault_checkpoint import (
    apply_native_motor_dropout, capture_fault_checkpoint,
    restore_native_fault_route_fresh, validate_fault_checkpoint,
)
from sat_sim.interactive.native_multiprocess_pilot import restore_two_process_clock_fresh
from sat_sim.interactive.native_state_pilot import (
    capture_state_subset, restore_state_subset_in_place,
)
from test_native_closed_loop_checkpoint import _build, _closed_loop_rows

CP_NS = 2_000_000_000
FAULT_END_NS = 3_200_000_000
STOP_NS = 5_000_000_000
STARTS = {"active": 1_800_000_000, "scheduled": 2_200_000_000}


def _drive_fault_source(graph, start_ns: int):
    sim = graph["sim"]
    if start_ns < CP_NS:
        sim.ConfigureStopTime(start_ns)
        sim.ExecuteSimulation()
        apply_native_motor_dropout(graph, active=True)
    sim.ConfigureStopTime(CP_NS)
    sim.ExecuteSimulation()


def _source_checkpoint(graph, kind: str) -> dict:
    start_ns = STARTS[kind]
    _drive_fault_source(graph, start_ns)
    fault = capture_fault_checkpoint(
        fault_id="rw-drop-1", checkpoint_ns=CP_NS,
        start_ns=start_ns, end_ns=FAULT_END_NS,
    )
    evidence = capture_closed_loop_evidence(
        sim=graph["sim"], processes=graph["processes"], tasks=graph["tasks"],
        spacecraft=graph["sc"], controller=graph["controller"],
        allocator=graph["allocator"], rw_effector=graph["rw"],
    )
    verify_closed_loop_evidence(evidence)
    expected_route = (
        graph["rw_fault_zero_message"] if kind == "active"
        else graph["allocator"].rwMotorTorqueOutMsg
    )
    # The native C++ input reader is linked, but has no read() API in 2.11.0.
    # Do not mistake source message contents for an actuator input readback:
    # the native dynamics and omission negative controls establish the effect.
    assert graph["rw"].rwMotorCmdInMsg.isLinked()
    if kind == "active":
        assert not any(abs(float(v)) > 1e-15
                       for v in expected_route.read().motorTorque)
    else:
        assert any(abs(float(v)) > 1e-10
                   for v in expected_route.read().motorTorque)
    return {
        "fault": fault,
        "evidence": evidence,
        "last_rw_motor_payload": [
            float(v) for v in graph["allocator"].rwMotorTorqueOutMsg.read().motorTorque
        ],
    }


def _continue_source(graph, kind: str):
    if kind == "scheduled":
        graph["sim"].ConfigureStopTime(STARTS[kind])
        graph["sim"].ExecuteSimulation()
        apply_native_motor_dropout(graph, active=True)
    graph["sim"].ConfigureStopTime(FAULT_END_NS)
    graph["sim"].ExecuteSimulation()
    apply_native_motor_dropout(graph, active=False)
    graph["sim"].ConfigureStopTime(STOP_NS)
    graph["sim"].ExecuteSimulation()
    return {
        "telemetry": _closed_loop_rows(graph, after_ns=CP_NS),
        "final_state": capture_state_subset(graph["sc"], expected_rw_count=3),
    }


def _worker(path: str, kind: str, fault_omission: str) -> None:
    from Basilisk.architecture import messaging
    from sat_sim.interactive.native_rw_fault_checkpoint import (
        apply_native_motor_dropout, restore_native_fault_route_fresh,
    )

    if kind not in STARTS or fault_omission not in (
        "none", "omit_checkpoint_active", "omit_activation", "omit_clear",
    ):
        raise ValueError("RW_FAULT_TEST_PROFILE_INVALID")
    package = json.loads(Path(path).read_text(encoding="utf-8"))
    report = package["fault"]
    context = package["evidence"]
    validated = validate_fault_checkpoint(report)
    verify_closed_loop_evidence(context)
    if (validated["checkpoint_ns"] != context["checkpoint_sim_time_ns"]
            or validated["start_ns"] != STARTS[kind]
            or validated["end_ns"] != FAULT_END_NS):
        raise ValueError("RW_FAULT_NATIVE_AND_JOURNAL_CLOCK_MISMATCH")

    graph = _build(closed_loop=True)
    if fault_omission != "omit_checkpoint_active":
        restore_native_fault_route_fresh(graph, report)
    restore_state_subset_in_place(
        graph["sc"], context["spacecraft_rw_state"], expected_rw_count=3,
    )
    restore_rw_effector_memory_fresh(
        graph["rw"], context["rw_effector_memory"],
    )
    restore_two_process_clock_fresh(
        graph["sim"], graph["processes"], graph["tasks"],
        graph["sc"], context["native_scheduler"],
    )
    fsw = context["fsw_integrator"]
    for key in ("K", "P", "Ki"):
        assert float(getattr(graph["controller"], key)) == fsw[key]
    assert graph["controller"].rwConfigParams.numRW == 3
    graph["controller"].priorTime = fsw["prior_time_ns"]
    graph["controller"].int_sigma = fsw["int_sigma"]
    graph["controller"].z = fsw["z"]
    payload = messaging.ArrayMotorTorqueMsgPayload()
    payload.motorTorque = package["last_rw_motor_payload"]
    graph["allocator"].rwMotorTorqueOutMsg.write(payload, fsw["prior_time_ns"])
    if kind == "scheduled":
        graph["sim"].ConfigureStopTime(STARTS[kind])
        graph["sim"].ExecuteSimulation()
        if fault_omission != "omit_activation":
            apply_native_motor_dropout(graph, active=True)
    graph["sim"].ConfigureStopTime(FAULT_END_NS)
    graph["sim"].ExecuteSimulation()
    if fault_omission != "omit_clear":
        apply_native_motor_dropout(graph, active=False)
    graph["sim"].ConfigureStopTime(STOP_NS)
    graph["sim"].ExecuteSimulation()
    print("RW_FAULT_RESTART_JSON:" + json.dumps({
        "pid": os.getpid(),
        "fault_omission": fault_omission,
        "resume_supported": False,
        "end_ns": int(graph["sim"].TotalSim.CurrentNanos),
        "telemetry": _closed_loop_rows(graph, after_ns=CP_NS),
        "final_state": capture_state_subset(graph["sc"], expected_rw_count=3),
    }, sort_keys=True), flush=True)


@pytest.mark.parametrize("kind", ["active", "scheduled"])
def test_native_rw_fault_lifecycle_cross_process_parity(tmp_path, kind) -> None:
    pytest.importorskip("Basilisk")
    source = _build(closed_loop=True)
    package = _source_checkpoint(source, kind)
    path = tmp_path / ("rw-dropout-" + kind + ".json")
    path.write_text(json.dumps(package, sort_keys=True), encoding="utf-8")
    expected = _continue_source(source, kind)
    fault_omission = (
        "omit_checkpoint_active" if kind == "active" else "omit_activation"
    )
    results = {}
    for mode in ("none", fault_omission, "omit_clear"):
        proc = subprocess.run([
            sys.executable, str(Path(__file__).resolve()),
            "--rw-fault-worker", str(path), kind, mode,
        ], capture_output=True, text=True, check=False, timeout=45)
        assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
        parsed = json.loads(next(
            row.removeprefix("RW_FAULT_RESTART_JSON:")
            for row in proc.stdout.splitlines()
            if row.startswith("RW_FAULT_RESTART_JSON:")
        ))
        assert parsed["pid"] != os.getpid()
        assert parsed["resume_supported"] is False
        assert parsed["end_ns"] == STOP_NS
        results[mode] = parsed
    actual = results["none"]
    for channel in (
        "attitude_mrp", "body_rate", "fsw_torque", "motor_torque", "rw_speeds",
    ):
        observed = actual["telemetry"][channel]
        reference = expected["telemetry"][channel]
        assert [t for t, _ in observed] == [t for t, _ in reference], channel
        assert len(reference) >= 3
        for idx, ((t, values), (_, baseline)) in enumerate(zip(observed, reference)):
            assert values == pytest.approx(baseline, abs=1e-8, rel=1e-11), (
                f"RW_FAULT_PARITY_FIRST_MISMATCH channel={channel} "
                f"index={idx} time_ns={t}"
            )
    for key, baseline in expected["final_state"]["vectors"].items():
        assert actual["final_state"]["vectors"][key] == pytest.approx(
            baseline, abs=1e-8, rel=1e-11
        ), key
    for wrong_mode in (fault_omission, "omit_clear"):
        wrong = results[wrong_mode]
        delta = max(abs(a - b) for a, b in zip(
            wrong["final_state"]["vectors"]["reaction_wheel_speed_rad_s"],
            expected["final_state"]["vectors"]["reaction_wheel_speed_rad_s"],
        ))
        assert delta > 1e-7, (kind, wrong_mode, delta)


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--rw-fault-worker":
        _worker(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        raise SystemExit("test-only native RW command dropout worker")
