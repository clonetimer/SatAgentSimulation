"""Real Basilisk independent RW Coulomb/viscous friction acceptance tests.

No mock satellite dynamics. The physical A/B reads the actual official
ReactionWheelStateEffector wheelSpeeds after constructing real factory wheels.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from sat_sim.interactive.continuous_initial_config import (
    ContinuousInitialConfiguration, initial_configuration_catalog,
)


def test_rw_friction_modes_defaults_ui_and_frontend_controls():
    assert ContinuousInitialConfiguration().native_values() == {}
    configured = ContinuousInitialConfiguration.model_validate({
        "adcs": {"rw_friction_mode": "per_wheel",
                 "rw_coulomb_friction_nm_0": 0.003,
                 "rw_viscous_friction_nms_2": 0.00008},
    })
    native = configured.native_values()
    assert native["rw_coulomb_friction_nm_0"] == pytest.approx(0.003)
    assert native["rw_viscous_friction_nms_2"] == pytest.approx(0.00008)
    fields = {f["name"]: f for group in initial_configuration_catalog()["groups"]
              if group["group"] == "adcs" for f in group["fields"]}
    for i in range(4):
        for kind, suffix, unit in (("coulomb", "nm", "N·m"), ("viscous", "nms", "N·m·s/rad")):
            key = f"rw_{kind}_friction_{suffix}_{i}"
            assert fields[key]["unit"] == unit
            assert fields[key]["default"] == (0. if i < 3 else None)
    html = (Path(__file__).resolve().parents[1] / "src/sat_sim/web/index.html").read_text(encoding="utf-8")
    js = (Path(__file__).resolve().parents[1] / "src/sat_sim/web/continuous.js").read_text(encoding="utf-8")
    assert '<option value="per_wheel">' in html
    assert "mode === 'per_wheel'" in js
    assert "updateRwFrictionNativeInitialLayout();" in js
    assert "i < 3 || four" in js
    for i in range(4):
        for kind, suffix in (("coulomb", "nm"), ("viscous", "nms")):
            assert f'data-native-key="rw_{kind}_friction_{suffix}_{i}"' in html


@pytest.mark.parametrize("bad", [
    {"rw_friction_mode": "per_wheel"},  # no friction at all
    {"rw_friction_mode": "per_wheel", "rw_coulomb_friction_nm_0": 0.003,
     "rw_coulomb_friction_nm": 0.001},  # shared/per-wheel conflict
    {"rw_friction_mode": "disabled", "rw_coulomb_friction_nm_0": 0.001},
    {"rw_friction_mode": "coulomb_viscous", "rw_coulomb_friction_nm": 0.001,
     "rw_viscous_friction_nms_1": .0001},
    {"rw_friction_mode": "per_wheel", "rw_coulomb_friction_nm_0": 0.02},
    {"rw_friction_mode": "per_wheel", "rw_coulomb_friction_nm_0": -1e-6},
    {"rw_friction_mode": "per_wheel", "rw_viscous_friction_nms_2": float("nan")},
    {"rw_friction_mode": "per_wheel", "rw_coulomb_friction_nm_0": float("inf")},
    {"rw_friction_mode": "per_wheel", "rw_coulomb_friction_nm_3": 0.001},
    {"rw_friction_mode": "per_wheel", "rw_viscous_friction_nms_3": 0.0},
])
def test_continuous_configuration_bad_per_wheel_friction_fails_closed(bad):
    with pytest.raises(ValidationError):
        ContinuousInitialConfiguration.model_validate({"adcs": bad})


def test_real_basilisk_independent_rw_friction_changes_actual_wheel_speed(monkeypatch):
    pytest.importorskip("Basilisk")
    from Basilisk.utilities import simIncludeRW
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    original_create = simIncludeRW.rwFactory.create
    observed = []
    def capture(self, *args, **kwargs):
        wheel = original_create(self, *args, **kwargs)
        observed.append({
            "label": kwargs["label"],
            "use": bool(kwargs["useRWfriction"]),
            "coulomb": float(getattr(wheel, "fCoulomb")),
            "viscous": float(getattr(wheel, "cViscous")),
        })
        return wheel
    monkeypatch.setattr(simIncludeRW.rwFactory, "create", capture)

    adapter = get_adapter_for_capability(WHOLE_ID)
    def run(values):
        start = len(observed)
        result = adapter.run(_short_spec(WHOLE_ID, duration_s=25., values={
            "initial_pointing_error_deg": 25.,
            **values,
        }))
        assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
        assert "reaction_wheels" in result.metadata["model_source_boundary"]["basilisk_official_native"]
        rw = observed[start:]
        assert len(rw) == 3
        return result.trace_rows, rw

    clean, clean_wheels = run({})
    friction, wheels = run({
        "rw_friction_mode": "per_wheel",
        "rw_coulomb_friction_nm_0": 0.006,
        "rw_viscous_friction_nms_1": 0.000002,
    })
    assert all(not row["use"] for row in clean_wheels)
    assert wheels[0]["label"] == "RW1" and wheels[0]["use"]
    assert wheels[0]["coulomb"] == pytest.approx(0.006)
    assert wheels[0]["viscous"] == pytest.approx(0.)
    assert wheels[1]["label"] == "RW2" and wheels[1]["use"]
    assert wheels[1]["coulomb"] == pytest.approx(0.)
    assert wheels[1]["viscous"] == pytest.approx(0.000002)
    assert wheels[2]["label"] == "RW3" and not wheels[2]["use"]
    assert [r["time_s"] for r in clean] == [r["time_s"] for r in friction]
    deltas = [
        abs(a["adcs.rw.speed_rad_s_0"] - b["adcs.rw.speed_rad_s_0"])
        for a, b in zip(clean[1:], friction[1:])
    ]
    assert max(deltas) > 1e-4, deltas


def test_real_four_wheel_independent_friction_and_legacy_shared_mode(monkeypatch):
    pytest.importorskip("Basilisk")
    from Basilisk.utilities import simIncludeRW
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    original_create = simIncludeRW.rwFactory.create
    observed = []
    def capture(self, *args, **kwargs):
        wheel = original_create(self, *args, **kwargs)
        observed.append((kwargs["label"], bool(kwargs["useRWfriction"]),
                         float(getattr(wheel, "fCoulomb")),
                         float(getattr(wheel, "cViscous"))))
        return wheel
    monkeypatch.setattr(simIncludeRW.rwFactory, "create", capture)
    adapter = get_adapter_for_capability(WHOLE_ID)
    def run(values):
        start = len(observed)
        adapter.run(_short_spec(WHOLE_ID, duration_s=4., values=values))
        return observed[start:]
    four = run({"rw_configuration": "pyramid_4", "rw_friction_mode": "per_wheel",
                "rw_coulomb_friction_nm_0": 1e-5,
                "rw_coulomb_friction_nm_3": 4e-5,
                "rw_viscous_friction_nms_2": 3e-7})
    assert len(four) == 4
    assert four[0][1] and four[0][2] == pytest.approx(1e-5)
    assert not four[1][1]
    assert four[2][1] and four[2][3] == pytest.approx(3e-7)
    assert four[3][1] and four[3][2] == pytest.approx(4e-5)
    shared = run({"rw_friction_mode": "coulomb_viscous",
                  "rw_coulomb_friction_nm": 2e-5,
                  "rw_viscous_friction_nms": 2e-7})
    assert len(shared) == 3
    assert all(row[1] and row[2] == pytest.approx(2e-5)
               and row[3] == pytest.approx(2e-7) for row in shared)


@pytest.mark.parametrize("values,error", [
    ({"rw_friction_mode": "per_wheel"}, "REQUIRES_NONZERO"),
    ({"rw_friction_mode": "per_wheel", "rw_coulomb_friction_nm_0": 0.02}, "INVALID"),
    ({"rw_friction_mode": "per_wheel", "rw_viscous_friction_nms_1": float("inf")}, "INVALID"),
    ({"rw_friction_mode": "per_wheel", "rw_viscous_friction_nms_0": "bad"}, "INVALID"),
    ({"rw_friction_mode": "disabled", "rw_coulomb_friction_nm_0": .001}, "MODE_MISMATCH"),
    ({"rw_friction_mode": "per_wheel", "rw_coulomb_friction_nm": .001,
      "rw_coulomb_friction_nm_0": .001}, "FORBIDS_SHARED"),
])
def test_direct_finite_runtime_rejects_illegal_per_wheel_modes(values,error):
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec
    with pytest.raises(ValueError, match="NATIVE_RW_PER_WHEEL_FRICTION_" + error):
        get_adapter_for_capability(WHOLE_ID).run(
            _short_spec(WHOLE_ID, duration_s=2., values=values)
        )


def test_direct_finite_three_wheel_rejects_any_ghost_rw4_friction():
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec
    with pytest.raises(ValueError, match="NATIVE_RW4_FRICTION_REQUIRES_PYRAMID_4"):
        get_adapter_for_capability(WHOLE_ID).run(_short_spec(
            WHOLE_ID, duration_s=2., values={
                "rw_friction_mode": "per_wheel",
                "rw_coulomb_friction_nm_0": .001, "rw_coulomb_friction_nm_3": 0.,
            },
        ))
