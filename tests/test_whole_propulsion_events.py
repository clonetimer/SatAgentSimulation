from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from sat_sim.capability_registry import get_adapter_for_capability, get_capability


WHOLE = "whole_spacecraft.unified_native.v1"


def _base_spec() -> dict:
    spec = yaml.safe_load(
        Path("examples/whole_spacecraft_unified_native_propulsion.yaml").read_text(
            encoding="utf-8"
        )
    )
    spec["simulation"].update({"duration_s": 16.0, "step_s": 0.2, "sample_s": 1.0})
    spec["parameters"]["values"].update(
        {
            "propulsion_burn_start_s": 5.0,
            "propulsion_burn_on_time_s": 4.0,
            "initial_soc": 0.9,
        }
    )
    spec["events"] = {}
    return spec


def _with_event(category: str, effect: str, **parameters: float) -> dict:
    spec = deepcopy(_base_spec())
    spec["events"] = {
        f"{category}s": [
            {
                "id": f"test_{effect}",
                "effect": effect,
                "target": "propulsion",
                "start_s": 4.0,
                "end_s": 10.0,
                "parameters": parameters,
            }
        ]
    }
    return spec


def _run(spec: dict):
    adapter = get_adapter_for_capability(WHOLE)
    contract = get_capability(WHOLE)
    assert not [
        issue for issue in adapter.validate(spec, contract.data)
        if issue.severity == "error"
    ]
    return adapter.run(spec, contract.data)


def test_propulsion_ignition_failure_propagates_across_whole_runtime() -> None:
    nominal = _run(_base_spec())
    failed = _run(
        _with_event("fault", "propulsion_thruster_ignition_failure")
    )

    assert nominal.summary["propellant_used_kg"] > 0.0
    assert nominal.summary["max_total_thrust_n"] > 0.0
    assert failed.summary["propellant_used_kg"] == pytest.approx(0.0)
    assert failed.summary["max_total_thrust_n"] == pytest.approx(0.0)
    assert failed.summary["propulsion_burn_observed"] is False
    assert failed.trace_rows[-1]["propulsion.fuel_mass_kg"] == pytest.approx(
        failed.trace_rows[0]["propulsion.fuel_mass_kg"]
    )
    assert abs(
        nominal.summary["final_orbit_radius_m"]
        - failed.summary["final_orbit_radius_m"]
    ) > 0.1
    episode = failed.metadata["fault_environment"]["episodes"][0]
    assert episode["evidence_status"] == "observed"
    assert episode["evidence_summary"]["physical_effect_verified"] is True


def test_propulsion_impulse_degradation_reduces_coupled_resource_activity() -> None:
    nominal = _run(_base_spec())
    degraded = _run(
        _with_event(
            "degradation",
            "propulsion_burn_impulse_loss",
            remaining_impulse_ratio=0.25,
        )
    )

    assert 0.0 < degraded.summary["propellant_used_kg"] < nominal.summary["propellant_used_kg"]
    nominal_power_samples = sum(
        float(row["propulsion.electrical_power_w"]) > 0.0
        for row in nominal.trace_rows
    )
    degraded_power_samples = sum(
        float(row["propulsion.electrical_power_w"]) > 0.0
        for row in degraded.trace_rows
    )
    assert 0 < degraded_power_samples < nominal_power_samples
    assert abs(
        nominal.summary["final_orbit_radius_m"]
        - degraded.summary["final_orbit_radius_m"]
    ) > 0.1
    active = [
        row
        for row in degraded.trace_rows
        if 4.0 <= float(row["time_s"]) <= 10.0
    ]
    assert active
    assert all(bool(row["label.degradation_active"]) for row in active)
    episode = degraded.metadata["fault_environment"]["episodes"][0]
    assert episode["evidence_status"] == "observed"
    assert episode["evidence_summary"]["physical_effect_verified"] is True


def test_propulsion_event_implicitly_enables_propulsion_graph() -> None:
    spec = _with_event("fault", "propulsion_thruster_ignition_failure")
    spec["parameters"]["values"]["propulsion_enabled"] = False
    result = _run(spec)
    assert result.summary["propulsion_enabled"] is True
    assert result.summary["max_total_thrust_n"] == pytest.approx(0.0)
