"""P0 genuine Basilisk reaction-wheel motor authority and command clamp closure.

No curve-only simulation: execute the real unified whole-spacecraft graph.
Verify official rwFactory SWIG u_max and actual recorded applied motor torques,
fault-manager limits, closed-loop wheel speed trajectories, and legacy defaults.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from sat_sim.interactive.continuous_initial_config import (
    ContinuousInitialConfiguration, initial_configuration_catalog,
)

KEY = "rw_max_torque_nm_"


def test_per_wheel_torque_prestart_registry_web_and_defaults():
    fields = {f["name"]: f for group in initial_configuration_catalog()["groups"]
              if group["group"] == "adcs" for f in group["fields"]}
    for i in range(4):
        assert fields[f"{KEY}{i}"]["unit"] == "N·m"
        assert fields[f"{KEY}{i}"]["default"] is None
    assert ContinuousInitialConfiguration().native_values() == {}
    cfg = ContinuousInitialConfiguration.model_validate({
        "adcs": {f"{KEY}0": .01, f"{KEY}1": .12},
    })
    assert cfg.native_values()[f"{KEY}0"] == pytest.approx(.01)
    assert cfg.native_values()[f"{KEY}1"] == pytest.approx(.12)
    web = Path(__file__).resolve().parents[1] / "src/sat_sim/web"
    html = (web / "index.html").read_text(encoding="utf-8")
    js = (web / "continuous.js").read_text(encoding="utf-8")
    assert 'id="continuousRwTorqueIndependentToggle"' in html
    assert "updateRwTorqueInitialLayout()" in js
    assert "enabled && (i < 3 || four)" in js
    for i in range(4):
        assert f'data-native-key="{KEY}{i}"' in html


@pytest.mark.parametrize("bad", [
    {f"{KEY}0": 0}, {f"{KEY}1": -.01}, {f"{KEY}2": .201},
    {f"{KEY}0": float("nan")}, {f"{KEY}1": float("inf")},
    {f"{KEY}3": .1},  # no fourth wheel in orthogonal_3
])
def test_prestart_rejects_invalid_or_nonexistent_hardware(bad):
    with pytest.raises(ValidationError):
        ContinuousInitialConfiguration.model_validate({"adcs": bad})


def test_real_native_per_wheel_motor_and_command_limit_saturate_actual_RW(monkeypatch):
    pytest.importorskip("Basilisk")
    from Basilisk.utilities import simIncludeRW
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    original = simIncludeRW.rwFactory.create
    physical_wheels = []
    def capture(self, *args, **kwargs):
        wheel = original(self, *args, **kwargs)
        physical_wheels.append((kwargs["label"], float(wheel.u_max)))
        return wheel
    monkeypatch.setattr(simIncludeRW.rwFactory, "create", capture)

    adapter = get_adapter_for_capability(WHOLE_ID)
    def run(values):
        start = len(physical_wheels)
        result = adapter.run(_short_spec(WHOLE_ID, duration_s=20., values={
            "initial_pointing_error_deg": 35.,
            **values,
        }))
        assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
        assert "reaction_wheels" in result.metadata["model_source_boundary"]["basilisk_official_native"]
        assert len(physical_wheels[start:]) == 3
        return result.trace_rows, physical_wheels[start:]

    baseline, base_rw = run({})
    changed, rw = run({f"{KEY}0": 0.0001, f"{KEY}1": 0.12, f"{KEY}2": 0.15})
    assert base_rw == [("RW1", .2), ("RW2", .2), ("RW3", .2)]
    assert [name for name, _ in rw] == ["RW1", "RW2", "RW3"]
    assert [v for _, v in rw] == pytest.approx([.0001, .12, .15])
    assert [r["time_s"] for r in baseline] == [r["time_s"] for r in changed]
    assert len(changed) >= 5
    for row in changed:
        for i, limit in enumerate((.0001, .12, .15)):
            assert row[f"adcs.rw.effective_max_torque_nm_{i}"] == pytest.approx(limit)
            assert row[f"adcs.rw.effective_torque_ratio_{i}"] == pytest.approx(1.)
            assert abs(row[f"adcs.control.applied_torque_nm_{i}"]) <= limit + 1e-9

    diffs = [abs(float(a["adcs.rw.speed_rad_s_0"]) -
                 float(b["adcs.rw.speed_rad_s_0"]))
             for a, b in zip(baseline[1:], changed[1:])]
    assert max(diffs) > 1e-5, diffs


def test_four_rw_physical_limits_and_legacy_shared_fallback(monkeypatch):
    pytest.importorskip("Basilisk")
    from Basilisk.utilities import simIncludeRW
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    original = simIncludeRW.rwFactory.create
    hardware = []
    def capture(self, *args, **kwargs):
        rw = original(self, *args, **kwargs)
        hardware.append(float(rw.u_max))
        return rw
    monkeypatch.setattr(simIncludeRW.rwFactory, "create", capture)
    adapter = get_adapter_for_capability(WHOLE_ID)
    def run(values):
        start = len(hardware)
        result = adapter.run(_short_spec(WHOLE_ID, duration_s=4., values=values))
        return result.trace_rows, hardware[start:]

    rows, physical = run({"rw_configuration": "pyramid_4", "rw_max_torque_nm": .16,
                          f"{KEY}0": .07, f"{KEY}3": .03})
    assert physical == pytest.approx([.07, .16, .16, .03])
    assert rows[-1]["adcs.rw.effective_max_torque_nm_3"] == pytest.approx(.03)
    assert rows[-1]["adcs.rw.effective_torque_ratio_3"] == pytest.approx(1.)
    _, legacy = run({"rw_max_torque_nm": .08})
    assert legacy == pytest.approx([.08, .08, .08])


@pytest.mark.parametrize("bad,error", [
    ({f"{KEY}0": 0}, "INVALID"),
    ({f"{KEY}0": .25}, "INVALID"),
    ({f"{KEY}1": float("inf")}, "INVALID"),
    ({f"{KEY}2": "invalid"}, "INVALID"),
    ({f"{KEY}0": True}, "INVALID"),
    ({f"{KEY}3": .05}, "RW4"),
])
def test_direct_finite_task_spec_rejects_invalid_native_motor_limit(bad,error):
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec
    error_code = ("NATIVE_RW4_TORQUE_LIMIT_REQUIRES_PYRAMID_4"
                  if error == "RW4" else "NATIVE_RW_PER_WHEEL_TORQUE_LIMIT_INVALID")
    with pytest.raises(ValueError, match=error_code):
        get_adapter_for_capability(WHOLE_ID).run(
            _short_spec(WHOLE_ID, duration_s=2., values=bad)
        )
