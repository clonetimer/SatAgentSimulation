"""P0-22 independent RW driver limits: actual Basilisk-scheduled torque A/B.

Genuine unified spacecraft/official Honeywell RW effects & official Battery.
The current limiter is an explicitly project-owned *driver SysModel*, not
a claim that Basilisk simulates electrical winding/drive electronics.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from sat_sim.interactive.continuous_initial_config import (
    ContinuousInitialConfiguration, initial_configuration_catalog,
)

I = "rw_motor_current_limit_a"
KT = "rw_motor_torque_constant_nm_per_a"


def test_independent_driver_prestart_catalog_ui_and_legacy_default():
    assert ContinuousInitialConfiguration().native_values() == {}
    catalog = {f["name"]: f for g in initial_configuration_catalog()["groups"]
               if g["group"] == "adcs" for f in g["fields"]}
    for i in range(4):
        assert catalog[f"{I}_{i}"]["unit"] == "A"
        assert catalog[f"{KT}_{i}"]["unit"] == "N·m/A"
        assert catalog[f"{I}_{i}"]["default"] is None
        assert catalog[f"{KT}_{i}"]["default"] is None
    config = ContinuousInitialConfiguration.model_validate({"adcs": {
        f"{I}_0": 0.005, f"{KT}_1": 0.001
    }})
    assert config.native_values()[f"{I}_0"] == pytest.approx(.005)
    assert config.native_values()[f"{KT}_1"] == pytest.approx(.001)
    html = (Path(__file__).resolve().parents[1] / "src/sat_sim/web/index.html").read_text(encoding="utf-8")
    js = (Path(__file__).resolve().parents[1] / "src/sat_sim/web/continuous.js").read_text(encoding="utf-8")
    assert 'id="continuousRwElectricalIndependentToggle"' in html
    assert "updateRwElectricalInitialLayout();" in js
    assert "independent && (i < 3 || four)" in js
    assert "field.dataset.electricalSeeded = 'true'" in js
    assert "if (shared) field.value = shared.value;" in js
    for i in range(4):
        assert f'id="continuousRwElectrical_{i}"' in html
        for key in (I, KT):
            assert f'data-native-key="{key}_{i}"' in html


@pytest.mark.parametrize("bad", [
    {f"{I}_0": -1.}, {f"{I}_1": 20.1}, {f"{I}_2": True},
    {f"{I}_0": "0.01"}, {f"{I}_1": float("inf")},
    {f"{KT}_0": 0.}, {f"{KT}_2": .3}, {f"{KT}_0": False},
    {f"{KT}_1": float("nan")}, {f"{KT}_0": "0.01"},
    {f"{I}_3": 0.}, {f"{KT}_3": .02},
])
def test_prestart_fails_closed_for_invalid_and_ghost_rw4(bad):
    with pytest.raises(ValidationError):
        ContinuousInitialConfiguration.model_validate({"adcs": bad})


def test_real_basilisk_independent_driver_clamps_actual_torque_and_current(monkeypatch):
    pytest.importorskip("Basilisk")
    from Basilisk.utilities import simIncludeRW
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    old_factory = simIncludeRW.rwFactory.create
    hardware = []
    def capture(self, *args, **kwargs):
        wheel = old_factory(self, *args, **kwargs)
        hardware.append(float(wheel.u_max))
        return wheel
    monkeypatch.setattr(simIncludeRW.rwFactory, "create", capture)
    adapter = get_adapter_for_capability(WHOLE_ID)
    def run(values):
        before = len(hardware)
        result = adapter.run(_short_spec(WHOLE_ID, duration_s=25., values={
            "initial_pointing_error_deg": 35., **values,
        }))
        assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
        assert "reaction_wheels" in result.metadata["model_source_boundary"]["basilisk_official_native"]
        assert hardware[before:] == pytest.approx([.2, .2, .2])
        return result.trace_rows

    nominal = run({})
    independently_limited = run({f"{I}_0": .005, f"{KT}_1": .001})
    assert len(nominal) == len(independently_limited) >= 5
    assert [r["time_s"] for r in nominal] == [r["time_s"] for r in independently_limited]
    limits = (.0001, .01, .2)
    kt = (.02, .001, .02)
    currents = (.005, 10., 10.)
    for row in independently_limited:
        for i in range(3):
            applied = float(row[f"adcs.control.applied_torque_nm_{i}"])
            assert row[f"adcs.rw.effective_max_torque_nm_{i}"] == pytest.approx(limits[i])
            assert abs(applied) <= limits[i] + 1e-9
            assert row[f"adcs.rw.motor_current_a_{i}"] == pytest.approx(applied / kt[i], abs=1e-9)
            assert abs(row[f"adcs.rw.motor_current_a_{i}"]) <= currents[i] + 1e-8
            assert row[f"adcs.rw.effective_torque_ratio_{i}"] == pytest.approx(limits[i] / .2)
    differences = [
        abs(a["adcs.rw.speed_rad_s_0"] - b["adcs.rw.speed_rad_s_0"])
        for a, b in zip(nominal[1:], independently_limited[1:])
    ]
    assert max(differences) > 1e-5, differences


def test_four_rw_independent_driver_works_with_asymmetric_native_u_max():
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec
    result = get_adapter_for_capability(WHOLE_ID).run(_short_spec(
        WHOLE_ID, duration_s=5., values={
            "rw_configuration": "pyramid_4",
            "rw_max_torque_nm_0": .003,
            "rw_max_torque_nm_3": .03,
            "rw_motor_current_limit_a": 2.,
            "rw_motor_torque_constant_nm_per_a": .02,
            f"{I}_0": 0., f"{I}_3": .5,
            f"{KT}_3": .006,
        }
    ))
    assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
    limits = (0., .04, .04, .003)
    for row in result.trace_rows:
        for i, cap in enumerate(limits):
            applied = float(row[f"adcs.control.applied_torque_nm_{i}"])
            assert abs(applied) <= cap + 1e-9
            assert row[f"adcs.rw.effective_max_torque_nm_{i}"] == pytest.approx(cap)
            kt = (.006 if i == 3 else .02)
            assert row[f"adcs.rw.motor_current_a_{i}"] == pytest.approx(applied / kt)
        assert row["adcs.rw.effective_torque_ratio_3"] == pytest.approx(.003 / .03)


def test_real_basilisk_single_rw_electrical_disable_reduces_actual_eps_load():
    pytest.importorskip("Basilisk")
    from test_native_rw_power_consistency import _run_basilisk
    original, _ = _run_basilisk({})
    disabled, _ = _run_basilisk({f"{I}_0": 0.})
    assert len(original) == len(disabled) >= 5
    assert [r["time_s"] for r in original] == [r["time_s"] for r in disabled]
    assert all(abs(float(r["adcs.control.applied_torque_nm_0"])) < 1e-12 for r in disabled)
    assert all(float(r["adcs.rw.effective_max_torque_nm_0"]) == 0. for r in disabled)
    # The battery consumes the same project shaft-work power bridge sourced
    # from real post-clamp RW torque (not a fake current*voltage estimate).
    deltas = [
        abs(float(a["eps.loads.adcs_power_w"]) - float(b["eps.loads.adcs_power_w"]))
        for a, b in zip(original[1:], disabled[1:])
    ]
    assert max(deltas) > .02, deltas
    assert max(abs(float(a["eps.net_power_w"]) - float(b["eps.net_power_w"]))
               for a, b in zip(original[1:], disabled[1:])) > .02


@pytest.mark.parametrize("bad,error", [
    ({f"{I}_0": -1.}, "INVALID"),
    ({f"{I}_1": True}, "INVALID"),
    ({f"{I}_0": float("nan")}, "INVALID"),
    ({f"{KT}_0": 0.}, "INVALID"),
    ({f"{KT}_1": "0.02"}, "INVALID"),
    ({f"{KT}_2": .21}, "INVALID"),
    ({f"{I}_3": 0.}, "RW4"),
    ({f"{KT}_3": .02}, "RW4"),
])
def test_direct_finite_task_spec_rejects_bad_per_wheel_driver(bad,error):
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec
    code = ("NATIVE_RW4_ELECTRICAL_REQUIRES_PYRAMID_4"
            if error == "RW4" else "NATIVE_RW_PER_WHEEL_ELECTRICAL_INVALID")
    with pytest.raises(ValueError, match=code):
        get_adapter_for_capability(WHOLE_ID).run(
            _short_spec(WHOLE_ID, duration_s=2., values=bad)
        )
