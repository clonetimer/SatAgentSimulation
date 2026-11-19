"""P10-A: official Basilisk ReactionWheelPower -> native EPS battery coupling.

The controller's raw requested torque is not physical wheel torque under
motor-current, torque, and event limits. The power model must consume the
actual RWConfigLogMsg emitted by reactionWheelStateEffector.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _run_basilisk(values):
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    spec = _short_spec(WHOLE_ID, duration_s=20.0, values={
        "initial_pointing_error_deg": 35.0,
        "rw_initial_speed_rad_s_0": 90.0,
        "rw_initial_speed_rad_s_1": 110.0,
        "rw_initial_speed_rad_s_2": 80.0,
        "adcs_power_w": 12.0,
        "adcs_drive_efficiency": 0.75,
        "adcs_max_power_w": 120.0,
        **values,
    })
    result = get_adapter_for_capability(WHOLE_ID).run(spec)
    assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
    native = set(result.metadata["model_source_boundary"]["basilisk_official_native"])
    assert {"reaction_wheels", "battery", "reaction_wheel_power_0", "reaction_wheel_power_1", "reaction_wheel_power_2"} <= native
    assert "project_adcs_control_power_bridge" not in result.metadata["model_source_boundary"]["engineering_proxy"]
    assert result.summary["adcs_power_model"]["implementation"] == "Basilisk.simulation.ReactionWheelPower"
    return result.trace_rows, result


def test_official_reaction_wheel_power_uses_actual_post_effector_torque_and_speed():
    reference, result = _run_basilisk({})
    disabled, disabled_result = _run_basilisk({"rw_motor_current_limit_a": 0.0})
    limited, limited_result = _run_basilisk({"rw_motor_current_limit_a": 0.005})

    assert [row["time_s"] for row in reference] == [row["time_s"] for row in disabled]
    assert [row["time_s"] for row in reference] == [row["time_s"] for row in limited]
    assert len(reference) >= 5

    # PDU status is available to the official power nodes before their update.
    for run in (reference, limited, disabled):
        for row in run:
            assert float(row["eps.battery_energy_j"]) > 0.0
            assert float(row["eps.pdu.adcs_enabled"]) == 1.0
            assert 0.0 <= float(row["eps.loads.adcs_power_w"]) <= 120.0 + 1e-6

    # Disabled current supply means the official effector applies zero motor
    # torque even though the native FSW still issues a non-zero request.
    assert all(
        abs(float(row[f"adcs.rw.actual_applied_torque_nm_{i}"])) < 1e-12
        for row in disabled for i in range(3)
    )
    assert any(
        abs(float(row[f"adcs.rw.command_torque_nm_{i}"])) > 1e-5
        for row in disabled for i in range(3)
    )
    assert [row["eps.loads.adcs_power_w"] for row in disabled] == pytest.approx(
        [12.0] * len(disabled), abs=1e-6
    )

    # Recompute the official module equation from the actual RW state
    # message fields (Omega and u_current), not the pre-effector command.
    for row in reference:
        expected = 12.0 + sum(
            abs(float(row[f"adcs.rw.actual_mechanical_power_w_{i}"])) / 0.75
            for i in range(3)
        )
        assert float(row["eps.loads.adcs_power_w"]) == pytest.approx(expected, abs=1e-5)

    assert max(float(row["eps.loads.adcs_power_w"]) for row in reference) > 12.5
    assert max(float(row["eps.loads.adcs_power_w"]) for row in limited) < max(
        float(row["eps.loads.adcs_power_w"]) for row in reference
    ) - 0.1

    # The native SimpleBattery consumes each official ReactionWheelPower
    # node; this is not a presentation-only or post-run accounting formula.
    net_deltas = [
        abs(float(a["eps.net_power_w"]) - float(b["eps.net_power_w"]))
        for a, b in zip(reference, disabled)
    ]
    assert max(net_deltas) > 0.1, net_deltas


def test_native_adcs_eps_graph_records_official_rw_power_message_wiring():
    code = (
        Path(__file__).resolve().parents[1]
        / "src" / "sat_sim" / "bsk_engine" / "unified_native.py"
    ).read_text(encoding="utf-8")
    assert "ReactionWheelPower.ReactionWheelPower()" in code
    assert "power_node.rwStateInMsg.subscribeTo(rw_state_msg)" in code
    assert "power_node.nodeStatusInMsg.subscribeTo(pdu.adcsStatusOutMsg)" in code
    assert "battery.addPowerNodeToModel(power_node.nodePowerOutMsg)" in code
    assert 'messages[f"rw_state_{wheel_index}"] = rw_state_msg' in code
    assert 'messages[f"adcs_power_{wheel_index}"] = power_node.nodePowerOutMsg' in code
    assert "AdcsControlPowerBridge" not in code
    assert "project_adcs_control_power_bridge" not in code
