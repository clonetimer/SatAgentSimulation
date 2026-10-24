"""TaskSpec genuine Basilisk 3/4-wheel nominal execution and provenance."""
from __future__ import annotations

import math
import pytest

from sat_sim.adapters.component_reaction_wheel import ReactionWheelAdapter


def _spec(n, **overrides):
    return {
        "task_id": "rw_cluster_test", "capability_id": "component.reaction_wheel.v1",
        "task_type": "component",
        "target": {"level": "component", "name": "reaction_wheel", "mode": "nominal"},
        "simulation": {"duration_s": 2., "sample_s": .1},
        "parameters": {
            "physics_backend": "basilisk_native", "num_wheels": n,
            "wheel_inertia_kg_m2": [.1] * n,
            "max_motor_torque_nm": [.005] + [.02] * (n - 1),
            "max_speed_rad_s": 1000., "initial_wheel_speeds_rad_s": 0.,
            "command_torque_nm": [.05] + [0.] * (n - 1), "damping_nms": 0.,
            **overrides,
        },
    }


@pytest.mark.parametrize("count", (3, 4))
def test_real_native_rw_cluster_component_3_4(count):
    pytest.importorskip("Basilisk")
    adapter = ReactionWheelAdapter()
    s = _spec(count)
    assert not [v for v in adapter.validate(s) if v.severity == "error"]
    result = adapter.run(s)
    assert result.summary["runtime_truth_status"] == "real_native_rw_spacecraft_recorded"
    assert result.summary["physics_backend"] == "basilisk_native"
    assert result.summary["num_wheels"] == count
    assert len(result.metadata["native_axes_b"]) == count
    assert result.metadata["native_provenance"] == "Basilisk.simulation.reactionWheelStateEffector"
    assert len(result.trace_rows) > 5
    for row in result.trace_rows:
        assert row["adcs.reaction_wheel.num_wheels"] == count
        for i in range(count):
            assert f"adcs.reaction_wheel.speed_rad_s_{i}" in row
            assert f"adcs.reaction_wheel.applied_torque_nm_{i}" in row
            assert f"adcs.reaction_wheel.friction_torque_nm_{i}" in row
            assert abs(row[f"adcs.reaction_wheel.applied_torque_nm_{i}"]) <= (
                .005 if i == 0 else .02
            ) + 1e-9
        assert row["label.health_state"] == "nominal"
        assert row["label.fault_active"] is False
    assert abs(result.trace_rows[-1]["adcs.reaction_wheel.speed_rad_s_0"]
               - result.trace_rows[0]["adcs.reaction_wheel.speed_rad_s_0"]) > .02
    assert max(abs(r["adcs.reaction_wheel.applied_torque_nm_0"]) for r in result.trace_rows) == pytest.approx(.005)


def test_four_wheel_native_inertia_and_true_vector_momentum():
    pytest.importorskip("Basilisk")
    adapter = ReactionWheelAdapter()
    regular = adapter.run(_spec(4, command_torque_nm=[0., .01, 0., 0.]))
    changed = adapter.run(_spec(4, command_torque_nm=[0., .01, 0., 0.],
                                wheel_inertia_kg_m2=[.1, .2, .1, .1]))
    a, b = regular.trace_rows[-1], changed.trace_rows[-1]
    assert abs(a["adcs.reaction_wheel.speed_rad_s_1"]) > abs(b["adcs.reaction_wheel.speed_rad_s_1"]) * 1.35

    axis = regular.metadata["native_axes_b"]
    row = regular.trace_rows[-1]
    h_b = [sum(.1 * row[f"adcs.reaction_wheel.speed_rad_s_{i}"] * axis[i][j]
               for i in range(4)) for j in range(3)]
    assert row["adcs.reaction_wheel.momentum_norm_nms"] == pytest.approx(
        math.sqrt(sum(x*x for x in h_b)), rel=1e-10)
    assert row["adcs.reaction_wheel.rotational_energy_j"] == pytest.approx(
        sum(.5 * .1 * row[f"adcs.reaction_wheel.speed_rad_s_{i}"]**2 for i in range(4)), rel=1e-10)


@pytest.mark.parametrize("count", (2, 5, 8))
def test_unsupported_native_wheel_count_rejected_without_fallback(count):
    adapter = ReactionWheelAdapter()
    s = _spec(count)
    assert [i for i in adapter.validate(s) if i.severity == "error"]
    with pytest.raises(ValueError, match="NATIVE_RW_UNSUPPORTED"):
        adapter.run(s)


def test_bad_native_vector_length_is_rejected_before_physics():
    s = _spec(4, command_torque_nm=[.01, .02, .03])
    issues = ReactionWheelAdapter().validate(s)
    assert any("num_wheels" in i.message for i in issues)
    with pytest.raises(ValueError, match="NATIVE_RW_VECTOR_LENGTH_INVALID"):
        ReactionWheelAdapter().run(s)
