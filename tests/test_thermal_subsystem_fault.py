from __future__ import annotations

from copy import deepcopy

from sat_sim.adapters.subsystem_thermal_basic_lumped import ThermalBasicLumpedAdapter
from sat_sim.capability_registry import get_capability


def _spec(mode: str, effect: str | None = None) -> dict:
    spec = {
        "task_id": f"thermal_{mode}",
        "task_type": "subsystem",
        "capability_id": "subsystem.thermal.basic_lumped.v1",
        "target": {"level": "subsystem", "name": "thermal", "mode": mode},
        "simulation": {"duration_s": 600.0, "sample_s": 60.0},
        "parameters": {
            "initial_bus_temp_c": 20.0,
            "initial_battery_temp_c": 18.0,
        },
        "modifiers": {},
    }
    if effect:
        spec["modifiers"]["faults"] = [{
            "modifier_type": effect,
            "start_s": 120.0,
            "duration_s": 180.0,
        }]
    return spec


def _at(result, time_s: float) -> dict:
    return next(row for row in result.trace_rows if row["time_s"] == time_s)


def test_thermal_fault_disables_heating_and_rejection_in_window() -> None:
    adapter = ThermalBasicLumpedAdapter()
    contract = get_capability(adapter.capability_id)
    nominal = adapter.run(_spec("nominal"), contract.data)
    fault_spec = _spec("fault", "heating_or_rejection_failure")
    assert not [issue for issue in adapter.validate(fault_spec, contract.data) if issue.severity == "error"]
    fault = adapter.run(fault_spec, contract.data)
    baseline = _at(nominal, 180.0)
    active = _at(fault, 180.0)
    assert active["label.fault_active"] is True
    assert active["thermal.radiator.rejected_heat_w"] < baseline["thermal.radiator.rejected_heat_w"]
    assert active["thermal.heater.power_w"] == 0.0
    assert _at(fault, 60.0)["label.fault_active"] is False
    assert _at(fault, 360.0)["label.fault_active"] is False
    assert _at(fault, 360.0)["thermal.radiator.degradation_factor"] == 1.0


def test_thermal_rejects_unknown_fault() -> None:
    adapter = ThermalBasicLumpedAdapter()
    spec = deepcopy(_spec("fault", "invented_thermal_fault"))
    issues = adapter.validate(spec, get_capability(adapter.capability_id).data)
    assert any(issue.severity == "error" and "unsupported" in issue.message for issue in issues)
