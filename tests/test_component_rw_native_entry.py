"""Opt-in RW native TaskSpec capability: no disguised Python fallback."""
from __future__ import annotations

import pytest

from sat_sim.adapters.component_reaction_wheel import ReactionWheelAdapter


def _spec(**params):
    return {
        "capability_id": "component.reaction_wheel.v1",
        "task_type": "component",
        "task_id": "rw_native_probe_case",
        "target": {"level": "component", "name": "reaction_wheel", "mode": "nominal"},
        "simulation": {"duration_s": 3.0, "sample_s": 0.1},
        "parameters": {"physics_backend": "basilisk_native", "num_wheels": 1,
                       "wheel_inertia_kg_m2": .1, "max_motor_torque_nm": .005,
                       "initial_wheel_speeds_rad_s": 0., "command_torque_nm": .05,
                       **params},
    }


def test_native_rejects_unsupported_modes_and_profiles_even_if_direct_run():
    adapter = ReactionWheelAdapter()
    for change in (
        {"target": {"level": "component", "name": "reaction_wheel", "mode": "fault"}},
        {"parameters": {**_spec()["parameters"], "num_wheels": 2}},
        {"parameters": {**_spec()["parameters"], "torque_profile_nm": [.01]}},
    ):
        spec = {**_spec(), **change}
        assert any(x.severity == "error" for x in adapter.validate(spec))
        with pytest.raises(ValueError, match="NATIVE_RW_UNSUPPORTED"):
            adapter.run(spec)


def test_native_never_silently_falls_back_on_runtime_failure(monkeypatch):
    from sat_sim.bsk_engine import native_rw_component
    def fail(**_):
        raise RuntimeError("NATIVE_RUNTIME_UNAVAILABLE")
    monkeypatch.setattr(native_rw_component, "run_native_rw_probe", fail)
    with pytest.raises(RuntimeError, match="NATIVE_RUNTIME_UNAVAILABLE"):
        ReactionWheelAdapter().run(_spec())


def test_reference_default_path_remains_project_owned():
    reference = _spec()
    reference["parameters"].pop("physics_backend")
    r = ReactionWheelAdapter().run(reference)
    assert r.summary["status"] == "complete"
    assert r.metadata["physics_backend"] == "project_reference"
    assert r.metadata["model_source_boundary"]["basilisk_physics_executed"] is False


def test_component_rw_opt_in_real_basilisk_physics():
    pytest.importorskip("Basilisk")
    adapter = ReactionWheelAdapter()
    spec = _spec()
    assert not [issue for issue in adapter.validate(spec) if issue.severity == "error"]
    result = adapter.run(spec)
    assert result.summary["physics_backend"] == "basilisk_native"
    assert result.summary["runtime_truth_status"] == "real_native_rw_spacecraft_recorded"
    assert result.metadata["native_provenance"] == "Basilisk.simulation.reactionWheelStateEffector"
    assert len(result.trace_rows) > 5
    speeds = [r["adcs.reaction_wheel.speed_rad_s_0"] for r in result.trace_rows]
    applied = [r["adcs.reaction_wheel.applied_torque_nm_0"] for r in result.trace_rows]
    assert abs(speeds[-1] - speeds[0]) > .02
    assert max(abs(u) for u in applied) == pytest.approx(.005, abs=1e-5)
    assert all(abs(u) <= .005 + 1e-8 for u in applied)
    assert all(r["label.health_state"] == "nominal" for r in result.trace_rows)
    assert all(r["label.fault_active"] is False for r in result.trace_rows)


def test_component_rw_real_native_viscous_damping_and_friction_logs():
    pytest.importorskip("Basilisk")
    adapter = ReactionWheelAdapter()
    no_friction = adapter.run(_spec(command_torque_nm=0., initial_wheel_speeds_rad_s=100., damping_nms=0.))
    with_friction = adapter.run(_spec(command_torque_nm=0., initial_wheel_speeds_rad_s=100., damping_nms=.0002))
    bare = no_friction.trace_rows[-1]
    altered = with_friction.trace_rows[-1]
    assert altered["adcs.reaction_wheel.damping_nms_0"] == pytest.approx(.0002)
    assert abs(altered["adcs.reaction_wheel.speed_rad_s_0"]) < abs(bare["adcs.reaction_wheel.speed_rad_s_0"]) - .03
    assert max(abs(r["adcs.reaction_wheel.friction_torque_nm_0"]) for r in with_friction.trace_rows) > .0001
    assert max(abs(r["adcs.reaction_wheel.friction_torque_nm_0"]) for r in no_friction.trace_rows) == pytest.approx(0.)
