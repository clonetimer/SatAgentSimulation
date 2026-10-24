"""P0 independent real Basilisk RW mounting geometry physical acceptance.

Pure validation is NOT counted as a physical pass: separate native closed-loop
A/B must show genuine wheel-axis changes alter the spacecraft's angular motion.
"""
from __future__ import annotations

from pathlib import Path
import math

import numpy as np
import pytest
from pydantic import ValidationError

from sat_sim.bsk_engine.rw_geometry import resolve_rw_geometry, standard_rw_axes
from sat_sim.interactive.continuous_initial_config import (
    ContinuousInitialConfiguration, initial_configuration_catalog,
)


def test_legacy_native_axes_and_location_remain_exact():
    for configuration in ("orthogonal_3", "pyramid_4"):
        original = standard_rw_axes(configuration)
        axes, positions = resolve_rw_geometry({"rw_mount_radius_m": 0.2}, original)
        assert axes == original
        assert np.allclose(positions, np.asarray(original) * 0.2, atol=0., rtol=1e-15)
    assert ContinuousInitialConfiguration().native_values() == {}


def test_prestart_3d_geometry_is_exposed_and_submitted_only_if_selected():
    cfg = ContinuousInitialConfiguration.model_validate({"adcs": {
        "rw_geometry_mode": "per_wheel",
        "rw_axis_b_x_0": math.sqrt(3.) / 2., "rw_axis_b_y_0": 0.5,
        "rw_axis_b_z_0": 0.,
        "rw_mount_b_x_m_0": 0.23, "rw_mount_b_y_m_0": -0.11,
    }})
    assert cfg.native_values()["rw_mount_b_x_m_0"] == pytest.approx(.23)
    fields = {f["name"]: f for group in initial_configuration_catalog()["groups"]
              if group["group"] == "adcs" for f in group["fields"]}
    assert fields["rw_geometry_mode"]["default"] == "legacy_axis_offset"
    for i in range(4):
        for component in "xyz":
            assert fields[f"rw_axis_b_{component}_{i}"]["default"] is None
            assert fields[f"rw_axis_b_{component}_{i}"]["unit"] == "1"
            assert fields[f"rw_mount_b_{component}_m_{i}"]["default"] is None
            assert fields[f"rw_mount_b_{component}_m_{i}"]["unit"] == "m"
    web = Path(__file__).resolve().parents[1] / "src/sat_sim/web"
    html = (web / "index.html").read_text(encoding="utf-8")
    js = (web / "continuous.js").read_text(encoding="utf-8")
    assert 'id="continuousRwGeometryEditor"' in html
    assert "resetRwGeometryAxesForTopology()" in js
    assert "updateRwGeometryInitialLayout()" in js
    for i in range(4):
        for component in "xyz":
            assert f'data-native-key="rw_axis_b_{component}_{i}"' in html
            assert f'data-native-key="rw_mount_b_{component}_m_{i}"' in html


@pytest.mark.parametrize("bad,error", [
    ({"rw_axis_b_x_0": 1.}, "REQUIRES_PER_WHEEL_MODE"),
    ({"rw_mount_b_z_m_0": 0.}, "REQUIRES_PER_WHEEL_MODE"),
    ({"rw_geometry_mode": "unknown"}, "MODE_INVALID"),
    ({"rw_geometry_mode": "per_wheel"}, "REQUIRES_OVERRIDES"),
    ({"rw_geometry_mode": "per_wheel", "rw_mount_radius_m": .1,
      "rw_mount_b_x_m_0": .1}, "FORBIDS_SHARED_RADIUS"),
    ({"rw_geometry_mode": "per_wheel", "rw_axis_b_x_0": 1.}, "AXIS_TRIPLE_REQUIRED"),
    ({"rw_geometry_mode": "per_wheel", "rw_axis_b_x_0": .5,
      "rw_axis_b_y_0": .5, "rw_axis_b_z_0": .5}, "AXIS_NOT_UNIT"),
    ({"rw_geometry_mode": "per_wheel", "rw_axis_b_x_0": 0.,
      "rw_axis_b_y_0": 1., "rw_axis_b_z_0": 0.}, "AXES_SINGULAR"),
    ({"rw_geometry_mode": "per_wheel", "rw_mount_b_x_m_0": .251}, "POSITION_INVALID"),
    ({"rw_geometry_mode": "per_wheel", "rw_mount_b_x_m_0": float("nan")}, "POSITION_INVALID"),
    ({"rw_geometry_mode": "per_wheel", "rw_mount_b_x_m_0": float("inf")}, "POSITION_INVALID"),
    ({"rw_geometry_mode": "per_wheel", "rw_mount_b_x_m_3": .01}, "RW4_GEOMETRY_REQUIRES_PYRAMID_4"),
    ({"rw_geometry_mode": "per_wheel", "rw_axis_b_z_3": 0.}, "RW4_GEOMETRY_REQUIRES_PYRAMID_4"),
])
def test_invalid_3d_geometry_fails_pydantic_and_shared_resolver(bad,error):
    # Pydantic may reject invalid bounds or Literal values before the
    # shared resolver produces its more specific native failure code.
    with pytest.raises(ValidationError):
        ContinuousInitialConfiguration.model_validate({"adcs": bad})
    with pytest.raises(ValueError, match=error):
        resolve_rw_geometry(bad, standard_rw_axes("orthogonal_3"))


def test_native_real_rw_axis_and_installation_points_change_actual_dynamics(monkeypatch):
    pytest.importorskip("Basilisk")
    from Basilisk.utilities import simIncludeRW
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    baseline = standard_rw_axes("orthogonal_3")
    captured = []
    create = simIncludeRW.rwFactory.create
    def capture(self, *args, **kwargs):
        wheel = create(self, *args, **kwargs)
        captured.append({
            "axis": np.asarray(wheel.gsHat_B, dtype=float).reshape(-1).tolist(),
            "position": np.asarray(wheel.rWB_B, dtype=float).reshape(-1).tolist(),
            "name": kwargs["label"],
        })
        return wheel
    monkeypatch.setattr(simIncludeRW.rwFactory, "create", capture)

    adapter = get_adapter_for_capability(WHOLE_ID)
    def run(params):
        start = len(captured)
        result = adapter.run(_short_spec(WHOLE_ID, duration_s=25., values={
            "initial_pointing_error_deg": 25., **params,
        }))
        assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
        assert "reaction_wheels" in result.metadata["model_source_boundary"]["basilisk_official_native"]
        installed = captured[start:]
        assert len(installed) == 3
        return result.trace_rows, installed

    clean, wheels = run({})
    for i in range(3):
        assert wheels[i]["name"] == f"RW{i+1}"
        assert wheels[i]["axis"] == pytest.approx(baseline[i], abs=1e-12)
        assert wheels[i]["position"] == pytest.approx([0., 0., 0.], abs=1e-12)

    rotated, installed = run({
        "rw_geometry_mode": "per_wheel",
        "rw_axis_b_x_0": math.sqrt(3.) / 2.,
        "rw_axis_b_y_0": 0.5, "rw_axis_b_z_0": 0.,
        "rw_mount_b_x_m_0": .25,
        "rw_mount_b_y_m_0": -.2, "rw_mount_b_z_m_0": .1,
    })
    assert installed[0]["axis"] == pytest.approx([math.sqrt(3.) / 2., .5, 0.], abs=1e-12)
    assert installed[0]["position"] == pytest.approx([.25, -.2, .1], abs=1e-12)
    for i in (1, 2):
        assert installed[i]["axis"] == pytest.approx(baseline[i], abs=1e-12)
    assert [row["time_s"] for row in clean] == [row["time_s"] for row in rotated]
    rate_keys = [f"adcs.body_rate_rad_s_{axis}" for axis in "xyz"]
    differences = [max(abs(float(a[k]) - float(b[k])) for k in rate_keys)
                   for a, b in zip(clean[1:], rotated[1:])]
    assert max(differences) > 1e-6, differences


def test_native_four_rw_custom_b_frame_installation_and_axes(monkeypatch):
    pytest.importorskip("Basilisk")
    from Basilisk.utilities import simIncludeRW
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec
    expected = standard_rw_axes("pyramid_4")
    captured = []
    create = simIncludeRW.rwFactory.create
    def capture(self, *args, **kwargs):
        wheel = create(self, *args, **kwargs)
        captured.append((np.asarray(wheel.gsHat_B, dtype=float).reshape(-1).tolist(),
                         np.asarray(wheel.rWB_B, dtype=float).reshape(-1).tolist()))
        return wheel
    monkeypatch.setattr(simIncludeRW.rwFactory, "create", capture)
    result = get_adapter_for_capability(WHOLE_ID).run(
        _short_spec(WHOLE_ID, duration_s=4., values={
            "rw_configuration": "pyramid_4",
            "rw_geometry_mode": "per_wheel",
            "rw_mount_b_z_m_3": .2,
            "rw_axis_b_x_3": 0., "rw_axis_b_y_3": 0., "rw_axis_b_z_3": 1.,
        })
    )
    assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
    assert len(captured) == 4
    for i in range(3):
        assert captured[i][0] == pytest.approx(expected[i], abs=1e-12)
    assert captured[3][0] == pytest.approx([0., 0., 1.], abs=1e-12)
    assert captured[3][1] == pytest.approx([0., 0., .2], abs=1e-12)


@pytest.mark.parametrize("bad,error", [
    ({"rw_geometry_mode": "per_wheel", "rw_axis_b_x_0": 1.}, "AXIS_TRIPLE_REQUIRED"),
    ({"rw_geometry_mode": "per_wheel", "rw_mount_b_x_m_0": -.3}, "POSITION_INVALID"),
    ({"rw_geometry_mode": "per_wheel", "rw_mount_b_y_m_0": float("inf")}, "POSITION_INVALID"),
    ({"rw_geometry_mode": "per_wheel", "rw_axis_b_x_0": 0.,
      "rw_axis_b_y_0": 1., "rw_axis_b_z_0": 0.}, "AXES_SINGULAR"),
    ({"rw_geometry_mode": "per_wheel", "rw_mount_b_z_m_3": 0.}, "RW4_GEOMETRY_REQUIRES_PYRAMID_4"),
])
def test_direct_native_task_spec_rejects_invalid_geometry_without_prestart(bad,error):
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec
    with pytest.raises(ValueError, match=error):
        get_adapter_for_capability(WHOLE_ID).run(
            _short_spec(WHOLE_ID, duration_s=2., values=bad)
        )
