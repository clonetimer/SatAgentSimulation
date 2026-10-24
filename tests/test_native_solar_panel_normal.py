"""P0 EPS panel normal: reject invalid geometry and prove real Basilisk power A/B."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from sat_sim.interactive.continuous_initial_config import (
    ContinuousInitialConfiguration, initial_configuration_catalog,
)


def test_fixed_body_frame_panel_normal_is_visible_and_forwarded():
    config = ContinuousInitialConfiguration.model_validate({
        "eps": {"solar_normal_b_x": 0.0, "solar_normal_b_y": 1.0,
                "solar_normal_b_z": 0.0},
    })
    assert config.native_values() == {
        "solar_normal_b_x": 0.0, "solar_normal_b_y": 1.0,
        "solar_normal_b_z": 0.0,
    }
    fields = {entry["name"]: entry for group in initial_configuration_catalog()["groups"]
              if group["group"] == "eps" for entry in group["fields"]}
    assert all(fields[f"solar_normal_b_{axis}"]["unit"] == "1" for axis in "xyz")
    assert [fields[f"solar_normal_b_{axis}"]["default"] for axis in "xyz"] == [1., 0., 0.]
    html = (Path(__file__).resolve().parents[1] / "src/sat_sim/web/index.html").read_text(encoding="utf-8")
    assert "不包含太阳翼铰链/电机力矩等硬件动力学" in html


@pytest.mark.parametrize("invalid", [
    {"solar_normal_b_x": 0.0},
    {"solar_normal_b_x": 0.0, "solar_normal_b_y": 0.0},
    {"solar_normal_b_x": 1.0, "solar_normal_b_y": 1.0},
    {"solar_normal_b_x": 1.1},
    {"solar_normal_b_x": float("nan")},
    {"solar_normal_b_z": float("inf")},
])
def test_reject_invalid_solar_normal_before_continuous_session(invalid):
    with pytest.raises(ValidationError):
        ContinuousInitialConfiguration.model_validate({"eps": invalid})


def test_real_basilisk_solar_normal_changes_generated_power():
    """Do not mock physics: use the same official Basilisk SimpleSolarPanel twice.

    Orbital initial position is near +X_N, with fixed analytical Sun at +X_N.
    At zero initial pointing error the +X_B panel sees daylight; -X_B sees
    the opposite hemisphere. Eclipse, area, efficiency and loads are identical.
    """
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    adapter = get_adapter_for_capability(WHOLE_ID)

    def run(normal_x):
        spec = _short_spec(WHOLE_ID, duration_s=6., values={
            "initial_pointing_error_deg": 0.,
            "solar_normal_b_x": normal_x,
            "solar_normal_b_y": 0.,
            "solar_normal_b_z": 0.,
        })
        result = adapter.run(spec)
        assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
        assert "solar_panel" in result.metadata["model_source_boundary"]["basilisk_official_native"]
        return [float(row["eps.solar_array_power_w"]) for row in result.trace_rows]

    facing = run(1.)
    away = run(-1.)
    assert len(facing) > 1 and len(away) > 1
    assert max(facing) > 1., facing
    assert max(away) < 1.e-6, away


def test_finite_runtime_rejects_non_unit_panel_normal_without_pydantic():
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    spec = _short_spec(WHOLE_ID, duration_s=2., values={
        "solar_normal_b_x": 0., "solar_normal_b_y": 0., "solar_normal_b_z": 0.,
    })
    with pytest.raises(ValueError, match="NATIVE_SOLAR_PANEL_NORMAL_B_NOT_UNIT"):
        get_adapter_for_capability(WHOLE_ID).run(spec)
