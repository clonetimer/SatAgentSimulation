from __future__ import annotations

import math
from typing import Any, Mapping

import pytest

from sat_sim.adapters.reference_public_satellite_case import PublicSatelliteReferenceCaseAdapter
from sat_sim.adapters.subsystem_thermal_reduced_order import ThermalReducedOrderAdapter
from sat_sim.adapters.whole_spacecraft_orbit_attitude_thermal import WholeSpacecraftOrbitAttitudeThermalAdapter
from sat_sim.form_schema import capability_form_schema
from sat_sim.task_models import to_runtime_task_spec
from sat_sim.unified_agent import normalize_form_task_spec
from subsystems.thermal.templates import load_thermal_template, thermal_template_inventory


def _runtime_default(capability_id: str) -> dict[str, Any]:
    form = capability_form_schema(capability_id)["default_form"]
    canonical = normalize_form_task_spec(form, task_id=f"recovery_{capability_id.replace('.', '_')}")
    return to_runtime_task_spec(canonical)


def _assert_trace_quality(rows: tuple[Mapping[str, Any], ...]) -> None:
    assert rows
    times = [float(row["time_s"]) for row in rows]
    assert times == sorted(times)
    assert len(times) == len(set(times))
    for row in rows:
        for value in row.values():
            if isinstance(value, float):
                assert math.isfinite(value)


def test_public_reference_default_runs_and_unknown_case_fails_closed() -> None:
    adapter = PublicSatelliteReferenceCaseAdapter()
    spec = _runtime_default("reference.public_satellite_case.v1")
    assert not [issue for issue in adapter.validate(spec) if issue.severity == "error"]
    result = adapter.run(spec)
    assert result.summary["status"] == "pass"
    assert result.summary["case_id"] == "public_1u_cubesat_thermal_power_orbit"
    assert len(result.trace_rows) == 1
    assert result.metadata["selected_case_asset"]["flight_validated"] is False

    unknown = dict(spec)
    unknown["parameters"] = {**dict(spec.get("parameters") or {}), "case_id": "unknown_case"}
    issues = adapter.validate(unknown)
    assert any(issue.severity == "error" and issue.path == "$.parameters.case_id" for issue in issues)
    with pytest.raises(ValueError, match="unknown public satellite case_id"):
        adapter.run(unknown)


def test_thermal_template_pack_contains_all_registered_templates() -> None:
    expected = {
        "nasa_single_node_leo_baseline",
        "cubesat_7node_box_template",
        "satmo_like_multiplanet_box_template",
    }
    inventory = thermal_template_inventory()
    assert inventory["template_count"] == 3
    assert {item["template_id"] for item in inventory["templates"]} == expected
    assert inventory["flight_validated"] is False
    for template_id in expected:
        template = load_thermal_template(template_id)
        assert template["source_case"]["claim_level"] == "public_reference_informed_not_flight_validated"
        assert template["source_case"]["flight_validated"] is False


def test_reduced_order_thermal_default_runs_with_usable_trace() -> None:
    spec = _runtime_default("subsystem.thermal_reduced_order.v1")
    adapter = ThermalReducedOrderAdapter()
    assert not [issue for issue in adapter.validate(spec) if issue.severity == "error"]
    result = adapter.run(spec)
    assert result.summary["status"] == "pass"
    assert result.summary["node_count"] == 7
    _assert_trace_quality(result.trace_rows)
    assert "thermal.node.internal.temp_c" in result.trace_rows[0]
    assert result.metadata["boundary"]["flight_validated"] is False


def test_orbit_attitude_thermal_default_runs_with_usable_trace() -> None:
    spec = _runtime_default("whole_spacecraft.orbit_attitude_thermal.v1")
    adapter = WholeSpacecraftOrbitAttitudeThermalAdapter()
    assert not [issue for issue in adapter.validate(spec) if issue.severity == "error"]
    result = adapter.run(spec)
    assert result.summary["status"] == "pass"
    assert result.summary["child_capability_id"] == "subsystem.thermal_reduced_order.v1"
    _assert_trace_quality(result.trace_rows)
    assert "thermal.node.internal.temp_c" in result.trace_rows[0]
    assert result.metadata["boundary"]["flight_validated"] is False


def test_default_form_uses_the_capability_single_accepted_backend() -> None:
    foundation = capability_form_schema("whole_spacecraft.bsksim_foundation.v1")["default_form"]
    assert foundation["simulation"]["backend"] == "basilisk"
    unified = capability_form_schema("whole_spacecraft.unified_native.v1")["default_form"]
    assert unified["simulation"]["backend"] == "basilisk"
    thermal = capability_form_schema("subsystem.thermal_reduced_order.v1")["default_form"]
    assert thermal["simulation"]["backend"] == "selective_unified_basilisk_assembly"
