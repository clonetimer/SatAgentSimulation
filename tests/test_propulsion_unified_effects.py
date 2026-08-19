from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from sat_sim.adapters.subsystem_propulsion_unified_native import PropulsionUnifiedNativeAdapter
from sat_sim.capability_registry import get_capability


def _spec(mode: str, kind: str | None = None, effect: str | None = None) -> dict:
    spec = yaml.safe_load(Path("examples/subsystem_propulsion_unified_native_nominal.yaml").read_text(encoding="utf-8"))
    spec["target"]["mode"] = mode
    if kind and effect:
        spec["modifiers"][kind] = [{
            "modifier_type": effect,
            "start_s": 0.2,
            "duration_s": 1.5,
            "severity": 0.8,
        }]
    return spec


def _at(result, time_s: float) -> dict:
    return min(result.trace_rows, key=lambda row: abs(float(row["time_s"]) - time_s))


def test_propulsion_unified_effects_apply_to_basilisk_thruster(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    adapter = PropulsionUnifiedNativeAdapter()
    contract = get_capability(adapter.capability_id)
    nominal = adapter.run(_spec("nominal"), contract.data)
    fault_spec = _spec("fault", "faults", "thrust_or_feed_failure")
    degradation_spec = _spec("degradation", "degradations", "performance_degradation")
    assert not [issue for issue in adapter.validate(fault_spec, contract.data) if issue.severity == "error"]
    assert not [issue for issue in adapter.validate(degradation_spec, contract.data) if issue.severity == "error"]
    fault = adapter.run(fault_spec, contract.data)
    degradation = adapter.run(degradation_spec, contract.data)

    baseline = _at(nominal, 1.0)
    fault_active = _at(fault, 1.0)
    degradation_active = _at(degradation, 1.0)
    assert fault_active["propulsion.thrust_force_n"] < baseline["propulsion.thrust_force_n"]
    assert degradation_active["propulsion.thrust_force_n"] < baseline["propulsion.thrust_force_n"]
    assert fault_active["label.fault_active"] is True
    assert degradation_active["label.degradation_active"] is True
    assert _at(fault, 2.0)["label.health_state"] == "nominal"
    for result in (fault, degradation):
        injection = result.summary["runtime_injection"]
        assert injection["actual_basilisk_application_count"] > 0
        assert injection["failed_direct_application_count"] == 0
        assert injection["unsupported_runtime_injection_count"] == 0


def test_propulsion_unified_rejects_unknown_effect() -> None:
    adapter = PropulsionUnifiedNativeAdapter()
    spec = deepcopy(_spec("fault", "faults", "invented_propulsion_fault"))
    issues = adapter.validate(spec, get_capability(adapter.capability_id).data)
    assert any(issue.severity == "error" and "unsupported" in issue.message for issue in issues)


def test_propulsion_fault_that_blocks_entire_burn_is_successful_injection() -> None:
    adapter = PropulsionUnifiedNativeAdapter()
    spec = _spec("fault", "faults", "thrust_or_feed_failure")
    spec["parameters"]["burn_start_s"] = 0.2
    spec["modifiers"]["faults"][0]["start_s"] = 0.2
    spec["modifiers"]["faults"][0]["duration_s"] = -1.0

    result = adapter.run(spec, get_capability(adapter.capability_id).data)

    assert result.summary["propellant_used_kg"] == 0.0
    assert result.summary["nominal_burn_status"] == "FAIL"
    assert result.summary["status"] == "PASS"
