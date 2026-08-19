from __future__ import annotations

from copy import deepcopy

from sat_sim.adapters.subsystem_source_native import PayloadSourceNativeAdapter
from sat_sim.capability_registry import get_capability


def _spec(mode: str, kind: str | None = None, effect: str | None = None) -> dict:
    spec = {
        "task_id": f"payload_{mode}",
        "task_type": "subsystem",
        "capability_id": "subsystem.payload.source_native.v1",
        "target": {"level": "subsystem", "name": "payload", "mode": mode},
        "simulation": {"duration_s": 10.0, "sample_s": 1.0},
        "parameters": {},
        "modifiers": {},
    }
    if kind and effect:
        spec["modifiers"][kind] = [{
            "modifier_type": effect,
            "start_s": 2.0,
            "duration_s": 6.0,
            "parameters": {"rate_per_s": 0.2, "max_fraction": 0.8},
        }]
    return spec


def _at(result, time_s: float) -> dict:
    return next(row for row in result.trace_rows if row["time_s"] == time_s)


def test_payload_fault_and_degradation_change_source_model_inputs() -> None:
    adapter = PayloadSourceNativeAdapter()
    contract = get_capability(adapter.capability_id)
    nominal = adapter.run(_spec("nominal"), contract.data)
    fault_spec = _spec("fault", "faults", "instrument_failure")
    degradation_spec = _spec("degradation", "degradations", "quality_or_rate_degradation")
    assert not [issue for issue in adapter.validate(fault_spec, contract.data) if issue.severity == "error"]
    assert not [issue for issue in adapter.validate(degradation_spec, contract.data) if issue.severity == "error"]
    fault = adapter.run(fault_spec, contract.data)
    degradation = adapter.run(degradation_spec, contract.data)
    baseline = _at(nominal, 6.0)
    fault_active = _at(fault, 6.0)
    degradation_active = _at(degradation, 6.0)
    assert fault_active["payload.source_native.enabled"] is False
    assert fault_active["payload.source_native.generated_bps"] < baseline["payload.source_native.generated_bps"]
    assert degradation_active["payload.source_native.generated_bps"] < baseline["payload.source_native.generated_bps"]
    assert fault_active["label.fault_active"] is True
    assert degradation_active["label.degradation_active"] is True
    assert _at(fault, 10.0)["label.health_state"] == "nominal"
    assert _at(degradation, 10.0)["label.health_state"] == "nominal"


def test_payload_rejects_unknown_effect() -> None:
    adapter = PayloadSourceNativeAdapter()
    spec = deepcopy(_spec("fault", "faults", "invented_payload_fault"))
    issues = adapter.validate(spec, get_capability(adapter.capability_id).data)
    assert any(issue.severity == "error" and "unsupported" in issue.message for issue in issues)
