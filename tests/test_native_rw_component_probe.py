"""Physical A/B — the genuine Basilisk RW/spacecraft dynamics must execute."""
from __future__ import annotations

import pytest
import json
from pathlib import Path

from sat_sim.bsk_engine.native_rw_component import run_native_rw_probe


def test_native_rw_input_gate_before_import():
    for params in (
        {"inertia_kg_m2": 0.0}, {"inertia_kg_m2": True},
        {"max_motor_torque_nm": -0.01}, {"max_speed_rad_s": 0.0},
        {"command_torque_nm": float("nan")},
        {"initial_speed_rad_s": 2000., "max_speed_rad_s": 1000.},
        {"step_s": 2., "duration_s": 1.},
    ):
        with pytest.raises(ValueError):
            run_native_rw_probe(**params)


def test_genuine_native_wheel_motor_and_inertia_a_b():
    pytest.importorskip("Basilisk")
    baseline = run_native_rw_probe(command_torque_nm=0.)
    driven = run_native_rw_probe(command_torque_nm=.01)
    double_inertia = run_native_rw_probe(inertia_kg_m2=.2, command_torque_nm=.01)
    clamped = run_native_rw_probe(command_torque_nm=.05, max_motor_torque_nm=.005)
    at_limit = run_native_rw_probe(command_torque_nm=.005, max_motor_torque_nm=.005)
    assert baseline.model_source == "Basilisk.simulation.reactionWheelStateEffector"
    assert baseline.times_s == driven.times_s == double_inertia.times_s == clamped.times_s == at_limit.times_s
    assert len(baseline.times_s) > 5
    assert baseline.factory_js_kg_m2 == pytest.approx(.1)
    assert double_inertia.factory_js_kg_m2 == pytest.approx(.2)
    assert clamped.factory_u_max_nm == pytest.approx(.005)
    assert baseline.factory_omega_max_rad_s == pytest.approx(1000.)
    # True wheel/hub dynamics: command response must differ from zero command.
    driven_delta = abs(driven.wheel_speed_rad_s[-1] - baseline.wheel_speed_rad_s[-1])
    heavier_delta = abs(double_inertia.wheel_speed_rad_s[-1] - baseline.wheel_speed_rad_s[-1])
    assert driven_delta > .1
    assert heavier_delta > .05
    assert heavier_delta < driven_delta * .8
    # True official RW u_max: a request 10x above max is indistinguishable
    # from a request exactly at max after the native motor saturation.
    assert clamped.wheel_speed_rad_s == pytest.approx(at_limit.wheel_speed_rad_s, abs=1e-5)
    assert abs(clamped.wheel_speed_rad_s[-1] - baseline.wheel_speed_rad_s[-1]) > .02

    # Persist source measurements only after native physical assertions passed.
    import Basilisk
    report = {
        "evidence_schema": "sat-sim.native-rw-physical-ab.v1",
        "runtime": {"package": "Basilisk", "version": Basilisk.__version__},
        "source": "Basilisk.simulation.reactionWheelStateEffector.rwSpeedOutMsg",
        "status": "PHYSICAL_AB_PASSED",
        "cases": {
            "zero_torque": baseline.as_dict(),
            "driven": driven.as_dict(),
            "double_inertia": double_inertia.as_dict(),
            "clamped_above_u_max": clamped.as_dict(),
            "command_at_u_max": at_limit.as_dict(),
        },
        "physical_comparisons": {
            "command_delta_rad_s": driven_delta,
            "double_inertia_delta_rad_s": heavier_delta,
            "inertia_response_ratio": heavier_delta / driven_delta,
            "saturated_limit_max_abs_difference_rad_s": max(
                abs(a - b) for a, b in zip(clamped.wheel_speed_rad_s, at_limit.wheel_speed_rad_s)
            ),
        },
        "limitations": [
            "Single wheel physically attached to a Basilisk hub only.",
            "Fault, degradation, EPS bridge and cross-level equivalence not yet tested.",
            "Not a selectable production TaskSpec component.",
        ],
    }
    target = Path("reports/native_rw_component_probe/physical_ab.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
