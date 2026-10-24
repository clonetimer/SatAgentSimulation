"""Actual Basilisk SWIG three-/four-wheel cluster perturbation and isolation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sat_sim.bsk_engine.native_rw_cluster import run_native_rw_cluster


def test_cluster_guards_before_native_import():
    for kwargs in (
        {"configuration": "two_wheels"},
        {"configuration": "orthogonal_3", "inertia_kg_m2": [0.1, 0.2]},
        {"configuration": "pyramid_4", "max_motor_torque_nm": [.01, .01, .01, False]},
        {"configuration": "orthogonal_3", "command_torque_nm": [0., 0., float("nan")]},
        {"configuration": "orthogonal_3", "max_speed_rad_s": [100., 0., 100.]},
        {"configuration": "pyramid_4", "initial_speed_rad_s": [0., 0., 0., 1001.]},
    ):
        with pytest.raises(ValueError):
            run_native_rw_cluster(**kwargs)


@pytest.mark.parametrize("configuration,count", [("orthogonal_3", 3), ("pyramid_4", 4)])
def test_real_native_cluster_per_wheel_command_and_hardware_limits(configuration, count):
    pytest.importorskip("Basilisk")
    zero = (0.,) * count
    enabled = (.01,) + (0.,) * (count - 1)
    huge = (.05,) + (0.,) * (count - 1)
    rated = (.005,) + (0.,) * (count - 1)
    cap = (.005,) + (.02,) * (count - 1)
    baseline = run_native_rw_cluster(configuration=configuration, command_torque_nm=zero, damping_nms=0.)
    driven = run_native_rw_cluster(configuration=configuration, command_torque_nm=enabled, damping_nms=0.)
    over = run_native_rw_cluster(configuration=configuration, command_torque_nm=huge, max_motor_torque_nm=cap, damping_nms=0.)
    limit = run_native_rw_cluster(configuration=configuration, command_torque_nm=rated, max_motor_torque_nm=cap, damping_nms=0.)

    assert len(baseline.axes_b) == count
    assert all(len(row) == count for row in driven.wheel_speed_rad_s)
    assert len(baseline.times_s) > 5
    assert baseline.times_s == driven.times_s == over.times_s == limit.times_s
    assert all(x == pytest.approx(.1) for x in driven.factory_js_kg_m2)
    assert over.factory_u_max_nm[0] == pytest.approx(.005)
    assert abs(driven.wheel_speed_rad_s[-1][0] - baseline.wheel_speed_rad_s[-1][0]) > .1
    # Real upstream u_max saturation, not Python-only torque display.
    for row in over.applied_motor_torque_nm:
        assert abs(row[0]) <= .005 + 1e-9
        assert all(abs(value) <= 1e-9 for value in row[1:])
    for saturated_row, rated_row in zip(over.wheel_speed_rad_s, limit.wheel_speed_rad_s):
        assert saturated_row == pytest.approx(rated_row, abs=1e-5)
    # No hidden fourth RW exists in the orthogonal 3 native graph.
    assert len(over.applied_motor_torque_nm[0]) == count


def test_real_four_wheel_independent_inertia_damping_and_axes():
    pytest.importorskip("Basilisk")
    from sat_sim.bsk_engine.rw_geometry import standard_rw_axes

    original = run_native_rw_cluster(
        configuration="pyramid_4", command_torque_nm=[0., .012, 0., 0.],
        inertia_kg_m2=[.1, .1, .1, .1], damping_nms=0.,
    )
    altered = run_native_rw_cluster(
        configuration="pyramid_4", command_torque_nm=[0., .012, 0., 0.],
        inertia_kg_m2=[.1, .2, .1, .1], damping_nms=0.,
    )
    coast = run_native_rw_cluster(
        configuration="pyramid_4", command_torque_nm=0.,
        initial_speed_rad_s=[0., 80., 0., 0.], damping_nms=0.,
    )
    viscous = run_native_rw_cluster(
        configuration="pyramid_4", command_torque_nm=0.,
        initial_speed_rad_s=[0., 80., 0., 0.],
        damping_nms=[0., .0002, 0., 0.],
    )
    assert original.axes_b == altered.axes_b == standard_rw_axes("pyramid_4")
    assert altered.factory_js_kg_m2 == pytest.approx((.1, .2, .1, .1))
    original_delta = abs(original.wheel_speed_rad_s[-1][1] - original.wheel_speed_rad_s[0][1])
    altered_delta = abs(altered.wheel_speed_rad_s[-1][1] - altered.wheel_speed_rad_s[0][1])
    assert original_delta > .1 and .35 < altered_delta / original_delta < .75
    assert abs(viscous.wheel_speed_rad_s[-1][1]) < abs(coast.wheel_speed_rad_s[-1][1]) - .02
    assert max(abs(row[1]) for row in viscous.measured_friction_torque_nm) > 1e-4
    assert max(abs(row[1]) for row in coast.measured_friction_torque_nm) == pytest.approx(0.)

    import Basilisk
    p = Path("reports/native_rw_cluster/physics_ab.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "schema": "sat-sim.native-rw-cluster-physics-ab.v1",
        "status": "PHYSICAL_AB_PASSED",
        "runtime": {"name": "Basilisk", "version": Basilisk.__version__},
        "contract": "native SWIG wheel state / actual applied motor torque / friction",
        "axes": [list(a) for a in original.axes_b],
        "cases": {
            "nominal": original.as_dict(), "double_rw2_inertia": altered.as_dict(),
            "rw2_coast": coast.as_dict(), "rw2_viscous": viscous.as_dict(),
        },
        "metrics": {"inertia_response_ratio": altered_delta / original_delta},
        "limitations": ["No FSW/EPS", "No wheel faults", "No cross-level physical parity yet"],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
