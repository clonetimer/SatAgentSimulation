from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
from typing import Any

import json
import pytest
import yaml

from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle


EVENT_START_S = 5.0
EVENT_END_S = 15.0
RUN_DURATION_S = 30.0


def _load_spec(subsystem: str, run_id: str, *, event_kind: str | None = None, effect: str | None = None) -> dict[str, Any]:
    example_name = (
        "subsystem_eps_unified_native_nominal.yaml"
        if subsystem == "eps"
        else "subsystem_comm_data_unified_native_nominal.yaml"
    )
    example = Path(__file__).parents[1] / "examples" / example_name
    spec = yaml.safe_load(example.read_text(encoding="utf-8"))
    spec["task"]["id"] = run_id
    spec["simulation"].update(duration_s=RUN_DURATION_S, step_s=0.2, sample_s=1.0)
    spec["outputs"]["telemetry_streams"] = []
    spec["assurance"]["allow_proxy"] = False
    if subsystem == "eps":
        spec["outputs"]["qoi"] = [
            "eps.battery_capacity_j",
            "eps.nominal_battery_capacity_j",
            "eps.battery_soc",
            "eps.solar_power_w",
            "eps.nominal_solar_power_w",
            "eps.payload_load_enabled_w",
            "eps.nominal_payload_load_w",
            "eps.net_power_w",
        ]
    else:
        spec["outputs"]["qoi"] = [
            "comm_data.storage_level_bits",
            "comm_data.transmitter_storage_node_baud_bps",
            "comm_data.transmitter_storage_node_rate_abs_bps",
            "comm_data.nominal_transmitter_storage_node_baud_bps",
        ]
    if event_kind and effect:
        spec["events"] = {"faults": [], "degradations": [], "constraints": []}
        spec["events"][f"{event_kind}s"] = [{
            "id": f"{effect}_event",
            "effect": effect,
            "target": subsystem,
            "start_s": EVENT_START_S,
            "end_s": EVENT_END_S,
            "magnitude": 0.8,
        }]
        spec["model"]["target"]["mode"] = event_kind
    return spec


def _run_case(
    tmp_path: Path,
    subsystem: str,
    run_id: str,
    *,
    event_kind: str | None = None,
    effect: str | None = None,
):
    pytest.importorskip("Basilisk", reason="requires official Basilisk 2.11.0")
    assert version("bsk") == "2.11.0"
    spec = _load_spec(subsystem, run_id, event_kind=event_kind, effect=effect)
    prepared = prepare_run(spec, output_root=tmp_path / "runs", run_id=run_id)
    executed = execute_prepared_run(
        prepared.bundle_root,
        expected_plan_sha256=prepared.execution_plan_sha256,
        max_attempts=1,
        hard_timeout=True,
    )
    assert executed.run_record.status.value == "SUCCEEDED", executed.run_record.model_dump(mode="json")
    assert executed.run_record.sealed is True
    assert executed.validation.result.value == "PASS", executed.validation.model_dump(mode="json")
    integrity = verify_run_bundle(prepared.bundle_root)
    assert integrity["ok"] is True, integrity
    assert executed.summary["runtime_truth_status"] == "instantiated_connected_recorded"
    assert executed.summary["external_or_proxy_module_count"] == 0
    if event_kind:
        assert len(executed.validation.injection_evidence) == 1
        evidence = executed.validation.injection_evidence[0]
        assert evidence.delivery_result.value == "PASS"
        assert evidence.effect_result.value == "PASS"
        injection = executed.summary["runtime_injection"]
        assert injection["actual_basilisk_application_count"] > 0
        assert injection["failed_direct_application_count"] == 0
        assert injection["unsupported_runtime_injection_count"] == 0
    return executed, integrity


def _at(rows, time_s: float) -> dict[str, Any]:
    return min(rows, key=lambda row: abs(float(row["time_s"]) - time_s))


@pytest.mark.parametrize(
    ("subsystem", "event_kind", "effect"),
    [
        ("eps", "fault", "generation_storage_distribution_failure"),
        ("eps", "degradation", "energy_margin_degradation"),
        ("comm_data", "fault", "transmitter_rate_loss"),
        ("comm_data", "degradation", "transmitter_rate_degradation"),
    ],
)
def test_eps_and_comm_data_effects_have_paired_native_basilisk_runs_and_sealed_bundles(
    tmp_path: Path,
    subsystem: str,
    event_kind: str,
    effect: str,
) -> None:
    """Each O2 effect must differ from a same-configuration nominal Run Bundle."""
    nominal_run_id = f"p9o2_{subsystem}_{event_kind}_nominal"
    injected_run_id = f"p9o2_{subsystem}_{event_kind}_injected"
    nominal, nominal_integrity = _run_case(tmp_path, subsystem, nominal_run_id)
    injected, injected_integrity = _run_case(
        tmp_path,
        subsystem,
        injected_run_id,
        event_kind=event_kind,
        effect=effect,
    )

    baseline = _at(nominal.trace_rows, 10.0)
    active = _at(injected.trace_rows, 10.0)
    if subsystem == "eps":
        assert active["eps.battery_capacity_j"] < baseline["eps.battery_capacity_j"]
        assert active["eps.solar_power_w"] < baseline["eps.solar_power_w"]
        assert active["eps.net_power_w"] < baseline["eps.net_power_w"]
        if event_kind == "fault":
            assert baseline["eps.payload_load_enabled_w"] > 0.0
            assert active["eps.payload_load_enabled_w"] == pytest.approx(0.0, abs=1e-9)
            assert "payload_channel_trip" in active["eps.shed_reason"]
            restored = _at(injected.trace_rows, 20.0)
            nominal_after = _at(nominal.trace_rows, 20.0)
            assert restored["eps.battery_capacity_j"] == pytest.approx(nominal_after["eps.battery_capacity_j"])
            assert restored["eps.solar_power_w"] == pytest.approx(nominal_after["eps.solar_power_w"], rel=1e-8)
            assert restored["eps.payload_load_enabled_w"] == pytest.approx(nominal_after["eps.payload_load_enabled_w"])
            assert restored["label.health_state"] == "nominal"
        else:
            assert active["eps.battery_capacity_j"] < active["eps.nominal_battery_capacity_j"]
            assert active["eps.solar_power_w"] < active["eps.nominal_solar_power_w"]
            later = _at(injected.trace_rows, 20.0)
            assert later["eps.battery_capacity_j"] < _at(nominal.trace_rows, 20.0)["eps.battery_capacity_j"]
            assert later["eps.solar_power_w"] < _at(nominal.trace_rows, 20.0)["eps.solar_power_w"]
    else:
        rate_field = "comm_data.transmitter_storage_node_rate_abs_bps"
        assert active[rate_field] < baseline["comm_data.nominal_transmitter_storage_node_baud_bps"]
        assert active["comm_data.storage_level_bits"] > baseline["comm_data.storage_level_bits"]
        if event_kind == "fault":
            restored = _at(injected.trace_rows, 20.0)
            nominal_after = _at(nominal.trace_rows, 20.0)
            assert restored[rate_field] == pytest.approx(nominal_after[rate_field], rel=1e-8)
            assert restored["label.health_state"] == "nominal"
        else:
            later = _at(injected.trace_rows, 20.0)
            assert later[rate_field] < _at(nominal.trace_rows, 20.0)[rate_field]


    report_path = Path("reports/native_subsystem_eps_comm_data/effect_ab.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": "p9-o2.native-effect-ab.v1", "cases": []}
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
    nominal_time = _at(nominal.trace_rows, 10.0)
    injected_time = _at(injected.trace_rows, 10.0)
    direct_fields = (
        ["eps.battery_capacity_j", "eps.solar_power_w", "eps.payload_load_enabled_w", "eps.net_power_w"]
        if subsystem == "eps"
        else ["comm_data.transmitter_storage_node_rate_abs_bps", "comm_data.storage_level_bits"]
    )
    report["cases"].append({
        "subsystem": subsystem,
        "event_kind": event_kind,
        "effect_id": effect,
        "nominal_run_id": nominal_run_id,
        "injected_run_id": injected_run_id,
        "nominal": {
            "status": nominal.run_record.status.value,
            "sealed": nominal.run_record.sealed,
            "validation": nominal.validation.result.value,
            "integrity": nominal_integrity,
        },
        "injected": {
            "status": injected.run_record.status.value,
            "sealed": injected.run_record.sealed,
            "validation": injected.validation.result.value,
            "integrity": injected_integrity,
            "runtime_injection": injected.summary.get("runtime_injection", {}),
        },
        "active_time_s": 10.0,
        "direct_recorder_comparison": {
            field: {"nominal": nominal_time.get(field), "injected": injected_time.get(field)}
            for field in direct_fields
        },
    })
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
