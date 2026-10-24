"""Genuine Basilisk reaction wheel electrical authority, not cosmetic telemetry.

The driver model is a Basilisk-scheduled project SysModel driving actual
official reaction-wheel C++ state effectors. It is NOT a motor EPS power model.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from sat_sim.interactive.continuous_initial_config import (
    ContinuousInitialConfiguration, initial_configuration_catalog,
)

CURRENT = "rw_motor_current_limit_a"
KT = "rw_motor_torque_constant_nm_per_a"


def test_native_electrical_driver_fields_operator_catalog_and_baseline():
    assert ContinuousInitialConfiguration().native_values() == {}
    fields = {
        f["name"]: f for group in initial_configuration_catalog()["groups"]
        if group["group"] == "adcs" for f in group["fields"]
    }
    assert fields[CURRENT]["unit"] == "A"
    assert fields[KT]["unit"] == "N·m/A"
    assert fields[CURRENT]["default"] == 10.
    assert fields[KT]["default"] == pytest.approx(.02)
    config = ContinuousInitialConfiguration.model_validate({
        "adcs": {CURRENT: .005, KT: .02}
    })
    assert config.native_values()[CURRENT] == pytest.approx(.005)
    assert config.native_values()[KT] == pytest.approx(.02)
    html = (Path(__file__).resolve().parents[1] / "src/sat_sim/web/index.html").read_text(encoding="utf-8")
    for field in (CURRENT, KT):
        assert f'data-native-key="{field}"' in html
    assert "不模拟电驱功耗" in html


@pytest.mark.parametrize("bad", [
    {CURRENT: -.1},
    {CURRENT: 20.1},
    {CURRENT: float("nan")},
    {KT: 0.},
    {KT: -.01},
    {KT: .21},
    {KT: float("inf")},
    {CURRENT: True},
    {KT: False},
    {CURRENT: "0.01"},
])
def test_invalid_electrical_prestart_rejected(bad):
    with pytest.raises(ValidationError):
        ContinuousInitialConfiguration.model_validate({"adcs": bad})


def test_real_basilisk_current_limit_changes_applied_torque_and_rw_spin(monkeypatch):
    pytest.importorskip("Basilisk")
    from Basilisk.utilities import simIncludeRW
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    official_create = simIncludeRW.rwFactory.create
    physical_rw = []
    def capture(self, *args, **kwargs):
        obj = official_create(self, *args, **kwargs)
        physical_rw.append(float(obj.u_max))
        return obj
    monkeypatch.setattr(simIncludeRW.rwFactory, "create", capture)

    adapter = get_adapter_for_capability(WHOLE_ID)
    def run(values):
        before = len(physical_rw)
        result = adapter.run(_short_spec(WHOLE_ID, duration_s=25., values={
            "initial_pointing_error_deg": 35., **values
        }))
        assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
        assert "reaction_wheels" in result.metadata["model_source_boundary"]["basilisk_official_native"]
        assert len(physical_rw) == before + 3
        assert len(result.trace_rows) >= 5
        return result.trace_rows, physical_rw[before:]

    baseline, nominal_hardware = run({})
    limited, unchanged_hardware = run({CURRENT: .005})
    via_kt, _ = run({CURRENT: .1, KT: .001})

    # Official SWIG hardware remains rated at u_max=0.2 N.m; only the
    # physical command path is reduced to the driver's electrical authority.
    assert nominal_hardware == pytest.approx([.2, .2, .2])
    assert unchanged_hardware == pytest.approx([.2, .2, .2])
    assert [row["time_s"] for row in baseline] == [row["time_s"] for row in limited]
    assert [row["time_s"] for row in limited] == [row["time_s"] for row in via_kt]
    for rows, electrical_max, constant, amps in (
        (baseline, .2, .02, 10.),
        (limited, .0001, .02, .005),
        (via_kt, .0001, .001, .1),
    ):
        for row in rows:
            for i in range(3):
                applied = float(row[f"adcs.control.applied_torque_nm_{i}"])
                assert abs(applied) <= electrical_max + 1e-9
                assert row[f"adcs.rw.effective_max_torque_nm_{i}"] == pytest.approx(electrical_max)
                assert row[f"adcs.rw.effective_torque_ratio_{i}"] == pytest.approx(electrical_max / .2)
                assert row[f"adcs.rw.motor_current_a_{i}"] == pytest.approx(applied / constant)
                assert abs(float(row[f"adcs.rw.motor_current_a_{i}"])) <= amps + 1e-8

    deviations = [
        abs(a["adcs.rw.speed_rad_s_0"] - b["adcs.rw.speed_rad_s_0"])
        for a, b in zip(baseline[1:], limited[1:])
    ]
    assert max(deviations) > 1e-5, deviations
    # Changing Kt/I with identical electrical torque cap changes current
    # consumption estimate, but not native mechanical torque response.
    assert [r["adcs.rw.speed_rad_s_0"] for r in limited] == pytest.approx(
        [r["adcs.rw.speed_rad_s_0"] for r in via_kt], rel=1e-8, abs=1e-8
    )


def test_real_basilisk_four_wheel_current_clamp_composes_with_per_wheel_hardware():
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    result = get_adapter_for_capability(WHOLE_ID).run(
        _short_spec(WHOLE_ID, duration_s=4., values={
            "rw_configuration": "pyramid_4",
            "rw_max_torque_nm_0": .00005,
            "rw_max_torque_nm_3": .0002,
            CURRENT: .005, KT: .02,  # electrical max .0001
        })
    )
    assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
    assert len(result.trace_rows) >= 2
    for row in result.trace_rows:
        for i, cap in enumerate((.00005, .0001, .0001, .0001)):
            assert row[f"adcs.rw.effective_max_torque_nm_{i}"] == pytest.approx(cap)
            assert abs(float(row[f"adcs.control.applied_torque_nm_{i}"])) <= cap + 1e-9
            assert abs(float(row[f"adcs.rw.motor_current_a_{i}"])) <= .005 + 1e-8


@pytest.mark.parametrize("bad", [
    {CURRENT: -1.},
    {CURRENT: float("inf")},
    {CURRENT: True},
    {CURRENT: "bad"},
    {KT: 0.},
    {KT: float("nan")},
    {KT: .3},
    {KT: False},
])
def test_direct_finite_rejects_illegal_electrical_parameters(bad):
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec
    with pytest.raises(ValueError, match="NATIVE_RW_ELECTRICAL_LIMIT_INVALID"):
        get_adapter_for_capability(WHOLE_ID).run(
            _short_spec(WHOLE_ID, duration_s=2., values=bad)
        )
