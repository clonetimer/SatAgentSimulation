from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml
import pytest

from sat_sim.adapters.subsystem_comm_data_unified_native import CommDataUnifiedNativeAdapter
from sat_sim.capability_registry import get_capability


def _spec(mode: str, kind: str | None = None, effect: str | None = None) -> dict:
    spec = yaml.safe_load(Path("examples/subsystem_comm_data_unified_native_nominal.yaml").read_text(encoding="utf-8"))
    spec["simulation"]["duration_s"] = 30.0
    spec["outputs"]["telemetry_streams"] = []
    spec["model"]["target"]["mode"] = mode
    if kind and effect:
        spec["events"] = {
            kind: [{
                "modifier_type": effect,
                "start_s": 10.0,
                "duration_s": 10.0,
                "magnitude": 0.8,
            }]
        }
    return spec


def _at(result, time_s: float) -> dict:
    return min(result.trace_rows, key=lambda row: abs(float(row["time_s"]) - time_s))


def test_comm_data_effects_apply_to_native_transmitter(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    adapter = CommDataUnifiedNativeAdapter()
    contract = get_capability(adapter.capability_id)
    nominal = adapter.run(_spec("nominal"), contract.data)
    fault_spec = _spec("fault", "faults", "transmitter_rate_loss")
    degradation_spec = _spec("degradation", "degradations", "transmitter_rate_degradation")
    assert not [issue for issue in adapter.validate(fault_spec, contract.data) if issue.severity == "error"]
    assert not [issue for issue in adapter.validate(degradation_spec, contract.data) if issue.severity == "error"]
    fault = adapter.run(fault_spec, contract.data)
    degradation = adapter.run(degradation_spec, contract.data)
    mild_fault_spec = _spec("fault", "faults", "transmitter_rate_loss")
    mild_fault_spec["events"]["faults"][0]["magnitude"] = 0.4
    mild_fault = adapter.run(mild_fault_spec, contract.data)

    baseline = _at(nominal, 15.0)
    fault_active = _at(fault, 15.0)
    degradation_active = _at(degradation, 15.0)
    mild_fault_active = _at(mild_fault, 15.0)
    field = "comm_data.transmitter_storage_node_baud_bps"
    nominal_rate = baseline["comm_data.nominal_transmitter_storage_node_baud_bps"]
    assert abs(fault_active[field]) == pytest.approx(nominal_rate * 0.48, rel=1e-8)
    assert abs(mild_fault_active[field]) == pytest.approx(nominal_rate * 0.74, rel=1e-8)
    assert abs(degradation_active[field]) < abs(baseline[field])
    assert abs(_at(fault, 25.0)[field]) == pytest.approx(abs(baseline[field]), rel=1e-8)
    assert fault_active["comm_data.storage_level_bits"] > baseline["comm_data.storage_level_bits"]
    assert degradation_active["comm_data.storage_level_bits"] > baseline["comm_data.storage_level_bits"]
    assert fault_active["label.fault_active"] is True
    assert degradation_active["label.degradation_active"] is True
    assert _at(fault, 25.0)["label.health_state"] == "nominal"
    for result in (fault, degradation):
        injection = result.summary["runtime_injection"]
        assert injection["actual_basilisk_application_count"] > 0
        assert injection["failed_direct_application_count"] == 0
        assert injection["unsupported_runtime_injection_count"] == 0
        assert injection["out_of_scope_runtime_injection_count"] == 0


def test_comm_data_rejects_unknown_and_legacy_broad_effects() -> None:
    adapter = CommDataUnifiedNativeAdapter()
    for effect in ("invented_comm_data_fault", "link_or_storage_failure"):
        spec = deepcopy(_spec("fault", "faults", effect))
        issues = adapter.validate(spec, get_capability(adapter.capability_id).data)
        assert any(issue.severity == "error" and "unsupported" in issue.message for issue in issues)


def test_comm_data_rejects_invalid_event_severity() -> None:
    adapter = CommDataUnifiedNativeAdapter()
    spec = _spec("fault", "faults", "transmitter_rate_loss")
    spec["events"]["faults"][0]["magnitude"] = 1.5
    issues = adapter.validate(spec, get_capability(adapter.capability_id).data)
    assert any(issue.severity == "error" and issue.code == "range" for issue in issues)
