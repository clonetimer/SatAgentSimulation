"""Native Basilisk 2.11 CSS gain/bias/Kelly/ADC saturation, physical A/B.

Reads genuine C++ coarseSunSensor.cssDataOutMsg.OutputData produced by the
unified spacecraft under Basilisk. No synthetic sensor response or fake noise.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from sat_sim.interactive.continuous_initial_config import (
    ContinuousInitialConfiguration, initial_configuration_catalog,
)

PARAMS = {
    "sun_sensor_scale_factor": (1., "scaleFactor"),
    "sun_sensor_bias_normalized": (0., "senBias"),
    "sun_sensor_kelly_factor": (0., "kellyFactor"),
    "sun_sensor_min_output": (0., "minOutput"),
    "sun_sensor_max_output": (1000000., "maxOutput"),
}

BORESIGHT = {
    "sun_sensor_normal_b_x": 1.,
    "sun_sensor_normal_b_y": 0.,
    "sun_sensor_normal_b_z": 0.,
}


def test_native_css_calibration_form_defaults_and_explicit_payload():
    assert ContinuousInitialConfiguration().native_values() == {}
    fields = {f["name"]: f for group in initial_configuration_catalog()["groups"]
              if group["group"] == "adcs" for f in group["fields"]}
    for key, (default, _) in PARAMS.items():
        assert fields[key]["default"] == default
        assert fields[key]["unit"] == "1"
    config = ContinuousInitialConfiguration.model_validate({
        "adcs": {"sun_sensor_scale_factor": 1.7,
                 "sun_sensor_bias_normalized": -.15,
                 "sun_sensor_kelly_factor": .4,
                 "sun_sensor_min_output": .1,
                 "sun_sensor_max_output": 2.},
    })
    assert all(key in config.native_values() for key in PARAMS)
    html = (Path(__file__).resolve().parents[1] / "src/sat_sim/web/index.html").read_text(encoding="utf-8")
    for key in PARAMS:
        assert f'data-native-key="{key}"' in html
    assert "不控制 EPS 太阳能板" in html


@pytest.mark.parametrize("bad", [
    {"sun_sensor_scale_factor": 0.},
    {"sun_sensor_scale_factor": 4.01},
    {"sun_sensor_bias_normalized": .51},
    {"sun_sensor_bias_normalized": float("nan")},
    {"sun_sensor_kelly_factor": -0.01},
    {"sun_sensor_kelly_factor": 1.01},
    {"sun_sensor_min_output": -.1},
    {"sun_sensor_max_output": 0.},
    {"sun_sensor_max_output": 1_000_001.},
    {"sun_sensor_min_output": .8, "sun_sensor_max_output": .7},
    {"sun_sensor_bias_normalized": True},
    {"sun_sensor_scale_factor": "2"},
])
def test_prestart_bad_css_calibration_fails_closed(bad):
    with pytest.raises(ValidationError):
        ContinuousInitialConfiguration.model_validate({"adcs": bad})


def test_real_basilisk_css_gain_bias_nonlinearity_and_adc_clamping(monkeypatch):
    pytest.importorskip("Basilisk")
    from Basilisk.simulation import coarseSunSensor
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    original = coarseSunSensor.CoarseSunSensor
    created = []
    def capture():
        instance = original()
        created.append(instance)
        return instance
    monkeypatch.setattr(coarseSunSensor, "CoarseSunSensor", capture)
    adapter = get_adapter_for_capability(WHOLE_ID)
    def run(params, normal=BORESIGHT):
        before = len(created)
        result = adapter.run(_short_spec(WHOLE_ID, duration_s=8., values={
            "initial_pointing_error_deg": 0., **normal, **params,
        }))
        assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
        assert "coarse_sun_sensor" in result.metadata["model_source_boundary"]["basilisk_official_native"]
        assert len(created) == before + 1
        assert len(result.trace_rows) >= 4
        return result.trace_rows, created[-1]

    baseline, nominal = run({})
    gain, gain_sensor = run({"sun_sensor_scale_factor": 1.7})
    biased, bias_sensor = run({"sun_sensor_bias_normalized": -.15})
    nonlinear, kelly_sensor = run({"sun_sensor_kelly_factor": .8})
    capped, capped_sensor = run({"sun_sensor_max_output": .2})
    floored, floor_sensor = run({"sun_sensor_min_output": .2}, {
        "sun_sensor_normal_b_x": -1.,
        "sun_sensor_normal_b_y": 0.,
        "sun_sensor_normal_b_z": 0.,
    })

    for key, (default, native) in PARAMS.items():
        assert float(getattr(nominal, native)) == pytest.approx(default)
    assert float(gain_sensor.scaleFactor) == pytest.approx(1.7)
    assert float(bias_sensor.senBias) == pytest.approx(-.15)
    assert float(kelly_sensor.kellyFactor) == pytest.approx(.8)
    assert float(capped_sensor.maxOutput) == pytest.approx(.2)
    assert float(floor_sensor.minOutput) == pytest.approx(.2)

    assert [row["time_s"] for row in baseline] == [row["time_s"] for row in gain]
    data = lambda rows: np.array([float(r["adcs.sun_sensor.cosine_output"]) for r in rows])
    b = data(baseline)
    g, biased_data, k = data(gain), data(biased), data(nonlinear)
    cap, floor = data(capped), data(floored)
    usable = np.flatnonzero((b > .35) & (b < .98))
    assert len(usable) > 0, b.tolist()
    assert g[usable] == pytest.approx(1.7 * b[usable], abs=1e-6, rel=1e-5)
    assert biased_data[usable] == pytest.approx(b[usable] - .15, abs=1e-6, rel=1e-5)
    assert max(np.abs(k[usable] - b[usable])) > .01
    assert np.all(cap <= .2 + 1e-10)
    assert cap[usable] == pytest.approx(np.full(len(usable), .2), abs=1e-10)
    assert floor == pytest.approx(np.full(len(floor), .2), abs=1e-10)

    # Negative control: CSS is only a sensor and cannot modify EPS sunlight
    # or solar-panel power if its electronics scale / bias are reconfigured.
    for key in ("eps.solar_array_power_w", "adcs.sun_sensor.raw_direction_b_x",
                "adcs.sun_sensor.raw_direction_b_y", "adcs.sun_sensor.raw_direction_b_z"):
        assert [r[key] for r in baseline] == pytest.approx(
            [r[key] for r in gain], abs=1e-9, rel=1e-9)


@pytest.mark.parametrize("bad,error", [
    ({"sun_sensor_scale_factor": 0.}, "CALIBRATION_INVALID"),
    ({"sun_sensor_scale_factor": float("inf")}, "CALIBRATION_INVALID"),
    ({"sun_sensor_bias_normalized": float("nan")}, "CALIBRATION_INVALID"),
    ({"sun_sensor_kelly_factor": True}, "CALIBRATION_INVALID"),
    ({"sun_sensor_min_output": "0"}, "CALIBRATION_INVALID"),
    ({"sun_sensor_max_output": 0.}, "CALIBRATION_INVALID"),
    ({"sun_sensor_min_output": 1., "sun_sensor_max_output": .3}, "OUTPUT_BOUNDS_INVALID"),
])
def test_direct_finite_css_calibration_parameter_guards(bad,error):
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec
    with pytest.raises(ValueError, match="NATIVE_SUN_SENSOR_" + error):
        get_adapter_for_capability(WHOLE_ID).run(
            _short_spec(WHOLE_ID, duration_s=2., values=bad)
        )
