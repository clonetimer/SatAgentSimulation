"""HR16 50/75/100 Nms rated-momentum slots wired to genuine Basilisk RW Js.

The A/B runs the *official* reactionWheelStateEffector, actual FSW and
spacecraft dynamics. Factory capture observes SWIG RW device instances,
not a synthetic motor-inertia or a Python-only satellite simulation.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from sat_sim.interactive.continuous_initial_config import (
    ContinuousInitialConfiguration, initial_configuration_catalog,
)

KEY = "rw_max_momentum_nms_"


def test_hr16_momentum_fields_and_3_vs_4_rw_contract():
    fields = {f["name"]: f for group in initial_configuration_catalog()["groups"]
              if group["group"] == "adcs" for f in group["fields"]}
    for i in range(4):
        assert fields[f"{KEY}{i}"]["unit"] == "N·m·s"
        assert fields[f"{KEY}{i}"]["default"] == (50 if i < 3 else None)
    assert ContinuousInitialConfiguration().native_values() == {}
    config = ContinuousInitialConfiguration.model_validate({
        "adcs": {"rw_configuration": "pyramid_4", **{f"{KEY}{i}": 100 for i in range(4)}},
    })
    assert all(config.native_values()[f"{KEY}{i}"] == 100 for i in range(4))
    html = (Path(__file__).resolve().parents[1] / "src/sat_sim/web/index.html").read_text(encoding="utf-8")
    javascript = (Path(__file__).resolve().parents[1] / "src/sat_sim/web/continuous.js").read_text(encoding="utf-8")
    for i in range(4):
        assert f'data-native-key="{KEY}{i}"' in html
    assert "continuousRw4MomentumLabel" in javascript
    assert "rw4Momentum.disabled = !four;" in javascript


@pytest.mark.parametrize("invalid", [
    {f"{KEY}0": 0}, {f"{KEY}1": 60}, {f"{KEY}2": 125},
    {f"{KEY}0": "unlimited"}, {f"{KEY}0": None},
    {f"{KEY}0": float("nan")}, {f"{KEY}0": float("inf")},
    {f"{KEY}3": 75},
])
def test_unapproved_or_nonexistent_hardware_rejected_at_prestart(invalid):
    with pytest.raises(ValidationError):
        ContinuousInitialConfiguration.model_validate({"adcs": invalid})


def test_real_basilisk_per_wheel_momentum_changes_spin_inertia_and_rw_speed(monkeypatch):
    pytest.importorskip("Basilisk")
    from Basilisk.utilities import simIncludeRW
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    original_create = simIncludeRW.rwFactory.create
    physical_wheels = []
    def record_factory(self, *args, **kwargs):
        real_rw = original_create(self, *args, **kwargs)
        physical_wheels.append({
            "rated_momentum": kwargs["maxMomentum"],
            "Js": float(real_rw.Js),
            "Omega_max": float(real_rw.Omega_max),
            "u_max": float(real_rw.u_max),
        })
        return real_rw
    monkeypatch.setattr(simIncludeRW.rwFactory, "create", record_factory)
    adapter = get_adapter_for_capability(WHOLE_ID)
    def run(momentum):
        start = len(physical_wheels)
        result = adapter.run(_short_spec(WHOLE_ID, duration_s=20., values={
            "initial_pointing_error_deg": 25.,
            f"{KEY}0": momentum,
        }))
        assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
        assert "reaction_wheels" in result.metadata["model_source_boundary"]["basilisk_official_native"]
        wheels = physical_wheels[start:]
        assert len(wheels) == 3
        assert [w["rated_momentum"] for w in wheels] == [momentum, 50., 50.]
        for w in wheels:
            # The HR16 type has a fixed 6000 RPM physical speed limit, and
            # rwFactory sets Js = maximum rated momentum / Omega_max.
            assert w["Js"] * w["Omega_max"] == pytest.approx(w["rated_momentum"], rel=1e-10)
        return result.trace_rows, wheels

    baseline, nominal = run(50)
    upgraded, higher = run(100)
    assert higher[0]["Js"] == pytest.approx(nominal[0]["Js"] * 2., rel=1e-10)
    assert higher[1]["Js"] == pytest.approx(nominal[1]["Js"], rel=1e-10)
    assert [r["time_s"] for r in baseline] == [r["time_s"] for r in upgraded]
    assert len(baseline) >= 5
    # Evidence from recorded REAL Basilisk RW states, not just factory kwargs.
    diffs = [abs(float(a["adcs.rw.speed_rad_s_0"]) - float(b["adcs.rw.speed_rad_s_0"]))
             for a, b in zip(baseline[1:], upgraded[1:])]
    assert max(diffs) > 1e-4, diffs


def test_four_wheel_hardware_rating_independence(monkeypatch):
    pytest.importorskip("Basilisk")
    from Basilisk.utilities import simIncludeRW
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    initial = simIncludeRW.rwFactory.create
    observed = []
    def track(self, *args, **kwargs):
        rw = initial(self, *args, **kwargs)
        observed.append((kwargs["label"], kwargs["maxMomentum"], float(rw.Js)))
        return rw
    monkeypatch.setattr(simIncludeRW.rwFactory, "create", track)
    result = get_adapter_for_capability(WHOLE_ID).run(
        _short_spec(WHOLE_ID, duration_s=4., values={
            "rw_configuration": "pyramid_4",
            f"{KEY}0": 50, f"{KEY}1": 75, f"{KEY}2": 100, f"{KEY}3": 75,
        })
    )
    assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
    assert [(n, h) for n, h, j in observed] == [
        ("RW1", 50.), ("RW2", 75.), ("RW3", 100.), ("RW4", 75.)
    ]
    assert observed[2][2] > observed[1][2] > observed[0][2]


@pytest.mark.parametrize("bad", [
    {f"{KEY}0": 60}, {f"{KEY}0": 0}, {f"{KEY}0": 50.001},
    {f"{KEY}0": float("inf")}, {f"{KEY}0": "disabled"},
])
def test_finite_native_bypassing_pydantic_rejects_invalid_factory_rating(bad):
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec
    with pytest.raises(ValueError, match="NATIVE_RW_HR16_MOMENTUM_UNSUPPORTED"):
        get_adapter_for_capability(WHOLE_ID).run(_short_spec(WHOLE_ID, duration_s=2., values=bad))


def test_three_wheel_runtime_rejects_ghost_rw4_rating():
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec
    with pytest.raises(ValueError, match="NATIVE_RW4_MOMENTUM_REQUIRES_PYRAMID_4"):
        get_adapter_for_capability(WHOLE_ID).run(_short_spec(WHOLE_ID, duration_s=2., values={f"{KEY}3": 75}))
