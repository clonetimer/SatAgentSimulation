"""DT-2/BE-15: RW driver deadband must change the real Basilisk wheel dynamics.

The deadband is an optional project SysModel upstream of official RW dynamics;
it is not a claim about actual motor hardware or an OEM internal deadband.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest
from pydantic import ValidationError
from sat_sim.interactive.continuous_initial_config import (
    ContinuousInitialConfiguration, initial_configuration_catalog,
)
from sat_sim.native_rw_preflight import validate_native_rw_settings

KEY = "rw_command_deadband_nm"


def test_rw_deadband_registry_form_and_disabled_baseline():
    assert ContinuousInitialConfiguration().native_values() == {}
    catalog = initial_configuration_catalog()
    fields = {f["name"]: f for group in catalog["groups"] if group["group"] == "adcs"
              for f in group["fields"]}
    assert fields[KEY]["default"] == 0.0
    assert fields[KEY]["unit"] == "N·m"
    assert ContinuousInitialConfiguration.model_validate(
        {"adcs": {KEY: 0.02}}).native_values()[KEY] == pytest.approx(0.02)
    html = (Path(__file__).resolve().parents[1] / "src/sat_sim/web/index.html").read_text(
        encoding="utf-8")
    assert f'data-native-key="{KEY}"' in html
    assert "项目 SysModel" in html
    validate_native_rw_settings({})
    validate_native_rw_settings({KEY: 0.0})
    validate_native_rw_settings({KEY: 0.2})


@pytest.mark.parametrize("invalid", [-0.001, .201, float("nan"), float("inf"),
                                      True, "0.01", None])
def test_invalid_deadband_fails_closed_before_basilisk(invalid):
    with pytest.raises(ValueError, match="NATIVE_RW_COMMAND_DEADBAND_INVALID"):
        validate_native_rw_settings({KEY: invalid})
    with pytest.raises(ValidationError):
        ContinuousInitialConfiguration.model_validate({"adcs": {KEY: invalid}})


def test_real_basilisk_deadband_changes_actual_motor_torque_and_rw_speed():
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    adapter = get_adapter_for_capability(WHOLE_ID)

    def run(values):
        result = adapter.run(_short_spec(
            WHOLE_ID, duration_s=25.0, values={
                "initial_pointing_error_deg": 35.0, **values,
            }))
        assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
        assert "reaction_wheels" in result.metadata["model_source_boundary"]["basilisk_official_native"]
        return list(result.trace_rows)

    baseline = run({})
    explicit_zero = run({KEY: 0.0})
    deadband = run({KEY: 0.2})
    assert len(baseline) == len(explicit_zero) == len(deadband) >= 5
    assert [x["time_s"] for x in baseline] == [x["time_s"] for x in deadband]
    for a, b in zip(baseline, explicit_zero):
        for wheel in range(3):
            for stem in ("adcs.control.applied_torque_nm_", "adcs.rw.speed_rad_s_"):
                assert b[f"{stem}{wheel}"] == pytest.approx(
                    a[f"{stem}{wheel}"], rel=1e-12, abs=1e-12)
    # The official RW hardware+driver limit is at most 0.2 N.m. At
    # deadband=0.2 N.m the bounded *drive* is therefore zero, while the
    # controller still publishes nonzero raw torque and native wheel speed
    # responds differently from the unchanged baseline.
    for row in deadband:
        for wheel in range(3):
            assert abs(float(row[f"adcs.control.applied_torque_nm_{wheel}"])) < 1e-12
    assert max(abs(float(row["adcs.rw.command_torque_nm_0"]))
               for row in deadband[1:]) > 1e-5
    delta = max(abs(float(a["adcs.rw.speed_rad_s_0"]) -
                    float(b["adcs.rw.speed_rad_s_0"]))
                for a, b in zip(baseline[1:], deadband[1:]))
    print(f"DT2_RW_DEADBAND_NATIVE_AB_MEASURED wheel0_speed_delta_max_rad_s={delta:.9e} "
          "fixture_deadband_nm=0.2 fixture_duration_s=25 zero_default_nm=0", flush=True)
    assert math.isfinite(delta) and delta > 1e-5


def test_real_four_rw_actuation_is_suppressed_by_explicit_deadband():
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    result = get_adapter_for_capability(WHOLE_ID).run(_short_spec(
        WHOLE_ID, duration_s=4.0, values={
            "rw_configuration": "pyramid_4",
            "initial_pointing_error_deg": 35.0, KEY: 0.2,
        }))
    assert len(result.trace_rows) >= 2
    for row in result.trace_rows:
        for wheel in range(4):
            assert abs(float(row[f"adcs.control.applied_torque_nm_{wheel}"])) < 1e-12
