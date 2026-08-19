from __future__ import annotations

import math

from sat_sim.capability_registry import get_adapter_for_capability, get_capability

CASES = (
    ("component.data_queue.v1", "data_queue", "read_stall", "throughput_decay", "comm.data_queue.downlink_bps", "lower"),
    ("component.heater.v1", "heater", "stuck_off", "heating_efficiency_decay", "thermal.heater.power_w", "lower"),
    ("component.link_budget.v1", "link_budget", "jamming", "margin_erosion", "comm.link_budget.effective_rate_bps", "lower"),
    ("component.onboard_storage.v1", "onboard_storage", "bad_block", "capacity_loss", "comm.storage.effective_capacity_bits", "lower"),
    ("component.payload.v1", "payload", "instrument_off", "sensitivity_decay", "payload.instrument.generated_bps", "lower"),
    ("component.payload_sensor.v1", "payload_sensor", "pixel_dropout", "responsivity_decay", "payload.sensor.effective_data_rate_bps", "lower"),
    ("component.power_sink.v1", "power_sink", "overload", "load_drift", "eps.power_sink.demand_w", "higher"),
    ("component.thermal_node.v1", "thermal_node", "thermal_runaway", "thermal_resistance_growth", "thermal.node.temp_k", "higher"),
)


def _spec(capability_id: str, target: str, mode: str, kind: str | None = None, effect: str | None = None) -> dict:
    modifiers: dict[str, list[dict]] = {}
    if kind and effect:
        modifiers[kind] = [{
            "modifier_type": effect,
            "start_s": 2.0,
            "duration_s": 6.0,
            "severity": 0.8,
            "parameters": {"rate_per_s": 0.2, "max_fraction": 0.8},
        }]
    return {
        "task_id": f"{target}_{mode}",
        "task_type": "component",
        "capability_id": capability_id,
        "target": {"level": "component", "name": target, "mode": mode},
        "simulation": {"duration_s": 10.0, "sample_s": 1.0},
        "parameters": {
            "generated_bps": 1000.0,
            "downlink_bps": 500.0,
            "node_temp_k": 280.0,
            "power_w": 10.0,
        },
        "modifiers": modifiers,
    }


def test_first_effect_batch_has_windowed_fault_and_degradation_physics() -> None:
    for capability_id, target, fault, degradation, field, direction in CASES:
        adapter = get_adapter_for_capability(capability_id)
        contract = get_capability(capability_id)
        nominal = adapter.run(_spec(capability_id, target, "nominal"), contract.data)
        fault_spec = _spec(capability_id, target, "fault", "faults", fault)
        degradation_spec = _spec(capability_id, target, "degradation", "degradations", degradation)
        assert not [issue for issue in adapter.validate(fault_spec, contract.data) if issue.severity == "error"]
        assert not [issue for issue in adapter.validate(degradation_spec, contract.data) if issue.severity == "error"]
        fault_result = adapter.run(fault_spec, contract.data)
        degradation_result = adapter.run(degradation_spec, contract.data)

        nominal_active = next(row for row in nominal.trace_rows if row["time_s"] == 6.0)
        fault_active = next(row for row in fault_result.trace_rows if row["time_s"] == 6.0)
        degradation_active = next(row for row in degradation_result.trace_rows if row["time_s"] == 6.0)
        assert fault_active["label.fault_active"] is True
        assert degradation_active["label.degradation_active"] is True
        assert next(row for row in fault_result.trace_rows if row["time_s"] == 0.0)["label.fault_active"] is False
        assert next(row for row in fault_result.trace_rows if row["time_s"] == 10.0)["label.fault_active"] is False
        assert all(
            math.isfinite(float(row[field]))
            for row in (*nominal.trace_rows, *fault_result.trace_rows, *degradation_result.trace_rows)
        )
        if direction == "lower":
            assert float(fault_active[field]) < float(nominal_active[field]), (capability_id, field)
            assert float(degradation_active[field]) < float(nominal_active[field]), (capability_id, field)
        else:
            assert float(fault_active[field]) > float(nominal_active[field]), (capability_id, field)
            assert float(degradation_active[field]) > float(nominal_active[field]), (capability_id, field)


def test_first_effect_batch_rejects_unknown_effects() -> None:
    for capability_id, target, *_ in CASES:
        adapter = get_adapter_for_capability(capability_id)
        spec = _spec(capability_id, target, "fault", "faults", "invented_effect")
        issues = adapter.validate(spec, get_capability(capability_id).data)
        assert any(issue.severity == "error" and "unsupported" in issue.message for issue in issues)
