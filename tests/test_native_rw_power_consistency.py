"""P0-21 cross-subsystem truth: real RW command -> ADCS bridge -> BSK battery.

The controller's raw requested torque is NOT physical motor torque under Imax,
Kt, per-wheel ratings, and actuator faults. This test runs the genuine
Basilisk unified spacecraft with no mock wheel, power-node, or battery.
"""
from __future__ import annotations

from pathlib import Path
import pytest


def _run_basilisk(values):
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec
    spec = _short_spec(WHOLE_ID, duration_s=20., values={
        "initial_pointing_error_deg": 35.,
        # Keep motor shaft speeds large enough to physically separate
        # post-clamp power from the old raw-controller-torque surrogate.
        "rw_initial_speed_rad_s_0": 90.,
        "rw_initial_speed_rad_s_1": 110.,
        "rw_initial_speed_rad_s_2": 80.,
        "adcs_power_w": 12.,
        "adcs_drive_efficiency": .75,
        "adcs_max_power_w": 120.,
        **values,
    })
    result = get_adapter_for_capability(WHOLE_ID).run(spec)
    assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
    native = result.metadata["model_source_boundary"]["basilisk_official_native"]
    assert "reaction_wheels" in native and "battery" in native
    return result.trace_rows, result


def test_native_power_bridge_uses_actual_electrically_limited_motor_message():
    reference, result = _run_basilisk({})
    disabled, disabled_result = _run_basilisk({"rw_motor_current_limit_a": 0.0})
    limited, limited_result = _run_basilisk({"rw_motor_current_limit_a": .005})

    assert [r["time_s"] for r in reference] == [r["time_s"] for r in disabled]
    assert [r["time_s"] for r in reference] == [r["time_s"] for r in limited]
    assert len(reference) >= 5
    # At time zero PDU (task priority 49) has not yet published its enable
    # status when the power bridge (priority 54) is initialized. Treat the
    # first sample as an unpowered handshake, not an energized ADCS interval.
    for run in (reference, limited, disabled):
        for row in run[1:]:
            assert float(row["eps.battery_energy_j"]) > 0.
            assert float(row["eps.pdu.adcs_enabled"]) == 1.
            assert float(row["eps.loads.adcs_power_w"]) >= 0.
            assert float(row["eps.loads.adcs_power_w"]) <= 120. + 1e-9

    # With a disabled motor current supply, the *real* applied motor torque
    # is zero although the official FSW still asks for nonzero torque.
    assert all(
        abs(float(r[f"adcs.control.applied_torque_nm_{i}"])) < 1e-12
        for r in disabled for i in range(3)
    )
    assert any(
        abs(float(r[f"adcs.rw.command_torque_nm_{i}"])) > 1e-5
        for r in disabled for i in range(3)
    )
    # This bridge explicitly models fixed ADCS bus/electronics power plus
    # RW mechanical shaft work / efficiency, NOT copper loss or V*I.
    assert [r["eps.loads.adcs_power_w"] for r in disabled[1:]] == pytest.approx(
        [12.] * len(disabled[1:]), abs=1e-9
    )
    assert max(float(r["eps.loads.adcs_power_w"]) for r in reference) > 12.5
    assert max(float(r["eps.loads.adcs_power_w"]) for r in limited) < max(
        float(r["eps.loads.adcs_power_w"]) for r in reference
    ) - .1
    # EPS battery consumes the bridge output through native
    # simpleBattery.addPowerNodeToModel, not a presentation-only formula.
    net_deltas = [
        abs(float(a["eps.net_power_w"]) - float(b["eps.net_power_w"]))
        for a,b in zip(reference, disabled)
    ]
    assert max(net_deltas) > .1, net_deltas


def test_native_adcs_to_eps_graph_records_applied_wheel_torque_source():
    # Catch regression even if no physical command is demanded in a new
    # scenario (the physical test above separately requires a real A/B).
    code = (Path(__file__).resolve().parents[1] /
            "src/sat_sim/bsk_engine/unified_native.py").read_text(encoding="utf-8")
    import re
    bridge = re.search(
        r'adcs_power_bridge = AdcsControlPowerBridge\\((.*?)\\n\\s*\\)',
        code, re.S,
    )
    assert bridge is not None
    assert "motor_torque_msg=rw_fault.commandOutMsg" in bridge.group(1)
    assert "motor_torque_msg=motor.rwMotorTorqueOutMsg" not in bridge.group(1)
    assert 'battery.addPowerNodeToModel(bridge.powerOutMsg)' in code
