"""Opt-in project tracking in the genuine Basilisk whole-spacecraft graph.

Important: this tests an attitude-aware, slew-bounded *kinematic controller*,
not an upstream Basilisk articulated panel or servo motor.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from pydantic import ValidationError

from sat_sim.interactive.continuous_initial_config import (
    ContinuousInitialConfiguration, initial_configuration_catalog,
)


def test_tracking_setup_defaults_forward_and_strict_bounds():
    fields = {f["name"]: f for g in initial_configuration_catalog()["groups"]
              if g["group"] == "eps" for f in g["fields"]}
    assert fields["solar_tracking_enabled"]["default"] is False
    assert fields["solar_tracking_max_slew_rad_s"]["default"] == 0.
    assert fields["solar_tracking_max_slew_rad_s"]["unit"] == "rad/s"
    assert ContinuousInitialConfiguration().native_values() == {}
    native = ContinuousInitialConfiguration.model_validate({"eps": {
        "solar_normal_b_x": -1., "solar_normal_b_y": 0., "solar_normal_b_z": 0.,
        "solar_tracking_enabled": True,
        "solar_tracking_max_slew_rad_s": .8,
    }}).native_values()
    assert native["solar_tracking_enabled"] is True
    assert native["solar_tracking_max_slew_rad_s"] == pytest.approx(.8)


@pytest.mark.parametrize("invalid", [
    {"solar_tracking_enabled": True},
    {"solar_tracking_enabled": True, "solar_tracking_max_slew_rad_s": 0.},
    {"solar_tracking_enabled": False, "solar_tracking_max_slew_rad_s": .2},
    {"solar_tracking_max_slew_rad_s": .5},
    {"solar_tracking_enabled": True, "solar_tracking_max_slew_rad_s": -0.1},
    {"solar_tracking_enabled": True, "solar_tracking_max_slew_rad_s": 1.01},
    {"solar_tracking_enabled": True, "solar_tracking_max_slew_rad_s": float("nan")},
    {"solar_tracking_enabled": True, "solar_tracking_max_slew_rad_s": float("inf")},
])
def test_tracking_configuration_invalid_inputs_fail_closed(invalid):
    with pytest.raises(ValidationError):
        ContinuousInitialConfiguration.model_validate({"eps": invalid})


def test_slew_on_sphere_antiparallel_and_normalization():
    pytest.importorskip("Basilisk")
    from sat_sim.bsk_engine.solar_tracking import slew_towards_normal
    a = [-1., 0., 0.]
    b = [1., 0., 0.]
    current = np.asarray(a)
    for _ in range(9):
        nxt = slew_towards_normal(current, b, .4)
        step = math.acos(float(np.clip(np.dot(current, nxt), -1., 1.)))
        assert step <= .400001
        assert np.linalg.norm(nxt) == pytest.approx(1., abs=1e-12)
        current = nxt
    assert current[0] > .99
    assert np.allclose(slew_towards_normal(a, b, 0.), a)


def test_genuine_basilisk_tracking_slews_panel_and_changes_real_power(monkeypatch):
    pytest.importorskip("Basilisk")
    from sat_sim.bsk_engine import solar_tracking
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    instances = []
    actual_tracker = solar_tracking.BasiliskScheduledSolarNormalTracker
    def capture_tracker(*args, **kwargs):
        tracker = actual_tracker(*args, **kwargs)
        instances.append(tracker)
        return tracker
    monkeypatch.setattr(solar_tracking, "BasiliskScheduledSolarNormalTracker", capture_tracker)

    adapter = get_adapter_for_capability(WHOLE_ID)
    def run(*, enable):
        params = {
            "initial_pointing_error_deg": 0.,
            "solar_normal_b_x": -1., "solar_normal_b_y": 0., "solar_normal_b_z": 0.,
            "solar_tracking_enabled": enable,
            "solar_tracking_max_slew_rad_s": .8 if enable else 0.,
        }
        result = adapter.run(_short_spec(WHOLE_ID, duration_s=8., values=params))
        assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
        assert "solar_panel" in result.metadata["model_source_boundary"]["basilisk_official_native"]
        return result, [float(row["eps.solar_array_power_w"]) for row in result.trace_rows]

    stationary, off = run(enable=False)
    tracking, on = run(enable=True)
    assert len(instances) == 1, "Disabled tracking must not instantiate the project controller"
    assert "project_solar_normal_tracker" in tracking.metadata["model_source_boundary"]["basilisk_project_native"]
    trace = list(instances[0].trace)
    assert len(trace) >= 3, "Tracking controller must actually execute in the Basilisk task"
    assert all(row["moved_rad"] <= .8 * row["dt_s"] + 1e-6 for row in trace)
    assert any(row["moved_rad"] > .01 for row in trace)
    assert max(off) < 1e-6, off
    assert max(on) > 1., on
    assert on[-1] > off[-1] + 1.
    assert tracking.summary["energy_bounds_pass"] is True
    assert stationary.summary["energy_bounds_pass"] is True


@pytest.mark.parametrize("values", [
    {"solar_tracking_enabled": True, "solar_tracking_max_slew_rad_s": 0.},
    {"solar_tracking_enabled": False, "solar_tracking_max_slew_rad_s": .1},
    {"solar_tracking_enabled": True, "solar_tracking_max_slew_rad_s": float("inf")},
    {"solar_tracking_enabled": "yes", "solar_tracking_max_slew_rad_s": .2},
])
def test_finite_whole_native_runtime_rejects_invalid_tracking_without_prestart_validator(values):
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec
    with pytest.raises(ValueError, match="NATIVE_SOLAR_TRACKING"):
        get_adapter_for_capability(WHOLE_ID).run(_short_spec(WHOLE_ID, duration_s=2., values=values))
