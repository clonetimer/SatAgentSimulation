"""Physical A/B — the genuine Basilisk RW/spacecraft dynamics must execute."""
from __future__ import annotations

import pytest

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
