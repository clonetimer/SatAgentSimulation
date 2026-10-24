"""Genuine upstream Basilisk CoarseSunSensor boresight/FOV physical acceptance.

No Python-generated sunlight telemetry. Inspect the actual official C++ CSS
sensor object and its live cssDataOutMsg in the unified whole-spacecraft task.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from sat_sim.bsk_engine.sun_sensor_geometry import resolve_sun_sensor_boresight
from sat_sim.interactive.continuous_initial_config import (
    ContinuousInitialConfiguration, initial_configuration_catalog,
)


def test_css_prestart_catalog_and_legacy_geometry():
    assert resolve_sun_sensor_boresight({}) == (0., 0., -1.)
    assert ContinuousInitialConfiguration().native_values() == {}
    cfg = ContinuousInitialConfiguration.model_validate({"adcs": {
        "sun_sensor_normal_b_x": 1.,
        "sun_sensor_normal_b_y": 0.,
        "sun_sensor_normal_b_z": 0.,
        "sun_sensor_fov_deg": 80.,
    }})
    for axis, val in zip("xyz", (1., 0., 0.)):
        assert cfg.native_values()[f"sun_sensor_normal_b_{axis}"] == val
    catalog = {f["name"]: f for group in initial_configuration_catalog()["groups"]
               if group["group"] == "adcs" for f in group["fields"]}
    for axis, default in zip("xyz", (0., 0., -1.)):
        key = f"sun_sensor_normal_b_{axis}"
        assert catalog[key]["unit"] == "1"
        assert catalog[key]["default"] == default
    assert catalog["sun_sensor_fov_deg"]["unit"] == "deg"
    assert catalog["sun_sensor_fov_deg"]["default"] == 80.
    html = (Path(__file__).resolve().parents[1] / "src/sat_sim/web/index.html").read_text(encoding="utf-8")
    for axis in "xyz":
        assert f'data-native-key="sun_sensor_normal_b_{axis}"' in html
    assert 'data-native-key="sun_sensor_fov_deg"' in html
    assert "不是太阳电池板法线" in html


@pytest.mark.parametrize("bad,error", [
    ({"sun_sensor_normal_b_x": 1.}, "TRIPLE_REQUIRED"),
    ({"sun_sensor_normal_b_x": 1., "sun_sensor_normal_b_y": 0.}, "TRIPLE_REQUIRED"),
    ({"sun_sensor_normal_b_x": 0., "sun_sensor_normal_b_y": 0.,
      "sun_sensor_normal_b_z": 0.}, "NOT_UNIT"),
    ({"sun_sensor_normal_b_x": .5, "sun_sensor_normal_b_y": .5,
      "sun_sensor_normal_b_z": .5}, "NOT_UNIT"),
    ({"sun_sensor_normal_b_x": float("nan"), "sun_sensor_normal_b_y": 0.,
      "sun_sensor_normal_b_z": 1.}, "INVALID"),
    ({"sun_sensor_normal_b_x": True, "sun_sensor_normal_b_y": 0.,
      "sun_sensor_normal_b_z": 0.}, "INVALID"),
    ({"sun_sensor_normal_b_x": 1.1, "sun_sensor_normal_b_y": 0.,
      "sun_sensor_normal_b_z": 0.}, "INVALID"),
])
def test_invalid_native_css_boresight_rejected_by_prestart_and_runtime_resolver(bad,error):
    with pytest.raises(ValidationError):
        ContinuousInitialConfiguration.model_validate({"adcs": bad})
    with pytest.raises(ValueError, match="NATIVE_SUN_SENSOR_NORMAL_" + error):
        resolve_sun_sensor_boresight(bad)


def test_actual_upstream_css_boresight_changes_raw_cosine_not_solar_panel(monkeypatch):
    pytest.importorskip("Basilisk")
    from Basilisk.simulation import coarseSunSensor
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    original = coarseSunSensor.CoarseSunSensor
    created = []
    def capture():
        obj = original()
        created.append(obj)
        return obj
    monkeypatch.setattr(coarseSunSensor, "CoarseSunSensor", capture)
    adapter = get_adapter_for_capability(WHOLE_ID)
    def run(values):
        start = len(created)
        result = adapter.run(_short_spec(WHOLE_ID, duration_s=8., values={
            "initial_pointing_error_deg": 0., **values,
        }))
        assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
        assert "coarse_sun_sensor" in result.metadata["model_source_boundary"]["basilisk_official_native"]
        assert len(created) == start + 1
        assert len(result.trace_rows) >= 4
        return result.trace_rows, created[-1]

    legacy, old = run({})
    facing, plus = run({"sun_sensor_normal_b_x": 1., "sun_sensor_normal_b_y": 0.,
                        "sun_sensor_normal_b_z": 0.})
    away, minus = run({"sun_sensor_normal_b_x": -1., "sun_sensor_normal_b_y": 0.,
                       "sun_sensor_normal_b_z": 0.})
    assert np.asarray(old.nHat_B, dtype=float).reshape(-1) == pytest.approx([0.,0.,-1.], abs=1e-12)
    assert np.asarray(plus.nHat_B, dtype=float).reshape(-1) == pytest.approx([1.,0.,0.], abs=1e-12)
    assert np.asarray(minus.nHat_B, dtype=float).reshape(-1) == pytest.approx([-1.,0.,0.], abs=1e-12)
    assert [r["time_s"] for r in legacy] == [r["time_s"] for r in facing] == [r["time_s"] for r in away]
    direct = [float(r["adcs.sun_sensor.cosine_output"]) for r in facing]
    reverse = [float(r["adcs.sun_sensor.cosine_output"]) for r in away]
    assert max(direct) > .3, direct
    assert max(reverse) < 1.e-8, reverse
    # The CSS is an observer: panel power and separately sourced sun-vector
    # output must remain unchanged when ONLY its boresight changes.
    for key in ("eps.solar_array_power_w",
                "adcs.sun_sensor.raw_direction_b_x",
                "adcs.sun_sensor.raw_direction_b_y",
                "adcs.sun_sensor.raw_direction_b_z"):
        assert [float(r[key]) for r in facing] == pytest.approx(
            [float(r[key]) for r in away], abs=1e-9, rel=1e-9)


def test_real_basilisk_css_fov_restricts_off_axis_response(monkeypatch):
    pytest.importorskip("Basilisk")
    from Basilisk.simulation import coarseSunSensor
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec
    original = coarseSunSensor.CoarseSunSensor
    created = []
    def capture():
        sensor = original()
        created.append(sensor)
        return sensor
    monkeypatch.setattr(coarseSunSensor, "CoarseSunSensor", capture)
    adapter = get_adapter_for_capability(WHOLE_ID)
    def run(fov):
        result = adapter.run(_short_spec(WHOLE_ID, duration_s=8., values={
            "initial_pointing_error_deg": 0.,
            "sun_sensor_normal_b_x": math.sqrt(3.) / 2.,
            "sun_sensor_normal_b_y": .5,
            "sun_sensor_normal_b_z": 0.,
            "sun_sensor_fov_deg": fov,
        }))
        assert len(result.trace_rows) >= 4
        return [float(r["adcs.sun_sensor.cosine_output"]) for r in result.trace_rows]
    open_fov = run(80.)
    narrow_fov = run(5.)
    assert float(created[0].fov) == pytest.approx(math.radians(80.))
    assert float(created[1].fov) == pytest.approx(math.radians(5.))
    assert max(open_fov) > .25, open_fov
    assert max(narrow_fov) < 1e-8, narrow_fov


@pytest.mark.parametrize("values,error", [
    ({"sun_sensor_normal_b_x": 1.}, "NATIVE_SUN_SENSOR_NORMAL_TRIPLE_REQUIRED"),
    ({"sun_sensor_normal_b_x": 0., "sun_sensor_normal_b_y": 0.,
      "sun_sensor_normal_b_z": 0.}, "NATIVE_SUN_SENSOR_NORMAL_NOT_UNIT"),
    ({"sun_sensor_normal_b_x": float("inf"), "sun_sensor_normal_b_y": 0.,
      "sun_sensor_normal_b_z": 0.}, "NATIVE_SUN_SENSOR_NORMAL_INVALID"),
    ({"sun_sensor_normal_b_x": True, "sun_sensor_normal_b_y": 0.,
      "sun_sensor_normal_b_z": 0.}, "NATIVE_SUN_SENSOR_NORMAL_INVALID"),
    ({"sun_sensor_fov_deg": 0.}, "NATIVE_SUN_SENSOR_FOV_INVALID"),
    ({"sun_sensor_fov_deg": float("nan")}, "NATIVE_SUN_SENSOR_FOV_INVALID"),
    ({"sun_sensor_fov_deg": "bad"}, "NATIVE_SUN_SENSOR_FOV_INVALID"),
])
def test_bypassing_continuous_prestart_direct_finite_rejects_invalid_css(values,error):
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec
    with pytest.raises(ValueError, match=error):
        get_adapter_for_capability(WHOLE_ID).run(
            _short_spec(WHOLE_ID, duration_s=2., values=values)
        )
