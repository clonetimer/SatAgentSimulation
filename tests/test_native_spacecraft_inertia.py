"""P0 body principal moments: native wiring and genuine Basilisk dynamics A/B.

Unlike schema-only checks, compare actual official Basilisk spacecraft and
closed-loop reaction-wheel trajectories at two valid principal-inertia sets.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from sat_sim.interactive.continuous_initial_config import (
    ContinuousInitialConfiguration, initial_configuration_catalog,
)

MOMENTS = ("xx", "yy", "zz")
DEFAULTS = (100., 80., 60.)


def test_orbit_inertia_fields_are_visible_and_forwarded():
    selection = {
        "spacecraft_inertia_xx_kg_m2": 200.,
        "spacecraft_inertia_yy_kg_m2": 160.,
        "spacecraft_inertia_zz_kg_m2": 120.,
    }
    cfg = ContinuousInitialConfiguration.model_validate({"orbit": selection})
    assert cfg.native_values() == selection
    fields = {
        f["name"]: f
        for group in initial_configuration_catalog()["groups"] if group["group"] == "orbit"
        for f in group["fields"]
    }
    for axis, expected in zip(MOMENTS, DEFAULTS):
        field = fields[f"spacecraft_inertia_{axis}_kg_m2"]
        assert field["default"] == expected
        assert field["unit"] == "kg·m²"
    html = (Path(__file__).resolve().parents[1] / "src/sat_sim/web/index.html").read_text(encoding="utf-8")
    for axis in MOMENTS:
        assert f'data-native-key="spacecraft_inertia_{axis}_kg_m2"' in html
    assert "不从质量自动估算" in html


@pytest.mark.parametrize("invalid", [
    {"spacecraft_inertia_xx_kg_m2": 0.},
    {"spacecraft_inertia_yy_kg_m2": -1.},
    {"spacecraft_inertia_zz_kg_m2": 10001.},
    {"spacecraft_inertia_xx_kg_m2": float("nan")},
    {"spacecraft_inertia_yy_kg_m2": float("inf")},
    {"spacecraft_inertia_xx_kg_m2": 500.},  # violates xx < yy+zz
    {"spacecraft_inertia_xx_kg_m2": 100., "spacecraft_inertia_yy_kg_m2": 30.,
     "spacecraft_inertia_zz_kg_m2": 70.},  # degenerate rigid-body triangle
])
def test_invalid_orbit_inertia_fails_at_continuous_configuration(invalid):
    with pytest.raises(ValidationError):
        ContinuousInitialConfiguration.model_validate({"orbit": invalid})


def test_genuine_basilisk_spacecraft_inertia_changes_closed_loop_attitude(monkeypatch):
    pytest.importorskip("Basilisk")
    from Basilisk.simulation import spacecraft
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    captured = []
    official_spacecraft = spacecraft.Spacecraft
    def capture_spacecraft():
        sc = official_spacecraft()
        captured.append(sc)
        return sc
    monkeypatch.setattr(spacecraft, "Spacecraft", capture_spacecraft)
    adapter = get_adapter_for_capability(WHOLE_ID)

    def run(moments):
        vals = {
            "initial_pointing_error_deg": 25.,
            **{f"spacecraft_inertia_{axis}_kg_m2": value
               for axis, value in zip(MOMENTS, moments)},
        }
        result = adapter.run(_short_spec(WHOLE_ID, duration_s=20., values=vals))
        assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
        assert "spacecraft" in result.metadata["model_source_boundary"]["basilisk_official_native"]
        sc = captured[-1]
        installed = np.asarray(sc.hub.IHubPntBc_B, dtype=float)
        assert np.allclose(installed, np.diag(moments), rtol=0., atol=1e-10)
        rows = result.trace_rows
        assert len(rows) >= 5
        return rows

    baseline = run(DEFAULTS)
    doubled = run(tuple(2. * value for value in DEFAULTS))
    assert len(captured) == 2
    assert [r["time_s"] for r in baseline] == [r["time_s"] for r in doubled]

    # Not just a constructor mutation: measured body rates and guidance must
    # change on actual Basilisk scheduler ticks with native FSW and RW active.
    rate_keys = [f"adcs.body_rate_rad_s_{axis}" for axis in "xyz"]
    rate_differences = [
        max(abs(float(a[key]) - float(b[key])) for key in rate_keys)
        for a, b in zip(baseline[1:], doubled[1:])
    ]
    pointing_differences = [
        abs(float(a["adcs.pointing_error_deg"]) - float(b["adcs.pointing_error_deg"]))
        for a, b in zip(baseline[1:], doubled[1:])
    ]
    assert max(rate_differences) > 1e-5, rate_differences
    assert max(pointing_differences) > 1e-5, pointing_differences


@pytest.mark.parametrize("invalid,error", [
    ({"spacecraft_inertia_xx_kg_m2": 0.}, "INVALID"),
    ({"spacecraft_inertia_xx_kg_m2": float("inf")}, "INVALID"),
    ({"spacecraft_inertia_yy_kg_m2": -2.}, "INVALID"),
    ({"spacecraft_inertia_xx_kg_m2": 600.}, "TRIANGLE_VIOLATION"),
])
def test_finite_native_runtime_rejects_invalid_body_inertia_without_pydantic(invalid,error):
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec
    with pytest.raises(ValueError, match=f"NATIVE_SPACECRAFT_PRINCIPAL_INERTIA_{error}"):
        get_adapter_for_capability(WHOLE_ID).run(
            _short_spec(WHOLE_ID, duration_s=2., values=invalid)
        )
