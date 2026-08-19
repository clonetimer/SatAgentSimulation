from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import os
import py_compile
import subprocess
import sys

from sat_sim.capability_agent import CapabilityTemplateBackend
from sat_sim.capability_planner import plan_capability_for_request
from sat_sim.capability_registry import get_adapter_for_capability, list_capabilities
from sat_sim.form_schema import capability_form_schema
from sat_sim.registered_capability_execution import REGISTERED_CAPABILITY_ADAPTER_KEY
from sat_sim.script_exporter import export_runner_script
from sat_sim.task_compiler import compile_task_spec
from sat_sim.task_validator import validate_task_spec
from sat_sim.unified_agent import normalize_form_task_spec
from sat_sim.unified_execution import (
    adapter_key_for_compiled,
    execute_compiled_task,
    reset_execution_composition_root_for_tests,
)


def _active_contracts():
    return [item for item in list_capabilities() if item.is_active and item.exposed_to_agent]


def _canonical_default(capability_id: str, *, suffix: str = "default") -> dict:
    form = deepcopy(capability_form_schema(capability_id)["default_form"])
    return normalize_form_task_spec(form, task_id=f"closure_{capability_id.replace('.', '_')}_{suffix}")


def _run_form(form: dict, capability_id: str, suffix: str):
    spec = normalize_form_task_spec(form, task_id=f"closure_{capability_id.replace('.', '_')}_{suffix}")
    validation = validate_task_spec(spec)
    assert validation.ok, validation.to_dict()
    reset_execution_composition_root_for_tests()
    result = execute_compiled_task(
        compile_task_spec(spec, validate=False),
        task_spec=spec,
        write_dataset=False,
    )
    return spec, result


def _mode_effects(payload: object, kind: str) -> set[str]:
    if not isinstance(payload, dict) or not payload.get("supported"):
        return set()
    for key in ("effects", f"{kind}_types"):
        value = payload.get(key)
        if isinstance(value, list):
            return {str(item) for item in value}
        if isinstance(value, dict):
            out: set[str] = set()
            for group in value.values():
                if isinstance(group, list):
                    out.update(str(item) for item in group)
            return out
    return set()


def test_all_active_defaults_close_form_compile_export_and_adapter_loading(tmp_path: Path) -> None:
    contracts = _active_contracts()
    assert len(contracts) == 43
    for contract in contracts:
        capability_id = contract.capability_id
        spec = _canonical_default(capability_id)
        assert spec["model"]["capability_id"] == capability_id
        validation = validate_task_spec(spec)
        assert validation.ok, {capability_id: validation.to_dict()}
        compiled = compile_task_spec(spec, validate=False)
        adapter = get_adapter_for_capability(capability_id)
        assert adapter.__class__.__module__

        capability_root = tmp_path / capability_id
        capability_root.mkdir(parents=True)
        spec_path = capability_root / "task_spec.json"
        import json
        spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        exported = export_runner_script(
            spec_path,
            capability_root / "run_capability.py",
            kind="capability-python",
            output_root=capability_root / "runs",
        )
        py_compile.compile(str(exported.output_path), doraise=True)

        implementation = contract.data.get("implementation") or {}
        execution = contract.data.get("execution") or {}
        assert implementation.get("uses_legacy_runner", False) is False
        assert execution.get("legacy_fallback_enabled", False) is False
        if not (compiled.metadata.get("model_asset_execution") or {}):
            assert adapter_key_for_compiled(compiled) == REGISTERED_CAPABILITY_ADAPTER_KEY


def test_generated_non_basilisk_capability_script_executes_end_to_end(tmp_path: Path) -> None:
    capability_id = "component.battery.v1"
    spec = _canonical_default(capability_id, suffix="generated_script")
    spec.setdefault("outputs", {})["output_root"] = str(tmp_path / "dataset")
    spec_path = tmp_path / "task_spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    exported = export_runner_script(
        spec_path,
        tmp_path / "run_capability.py",
        kind="capability-python",
        output_root=tmp_path / "dataset",
    )
    env = dict(os.environ)
    src_root = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = src_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    completed = subprocess.run(
        [sys.executable, str(exported.output_path), "--output-root", str(tmp_path / "dataset")],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["capability_id"] == capability_id
    assert payload["dataset"] is not None
    assert Path(payload["dataset"]["output_root"]).exists()
    assert (Path(payload["dataset"]["output_root"]) / "manifest.json").is_file()


def test_exact_capability_id_is_authoritative_for_planner_and_template_backend() -> None:
    backend = CapabilityTemplateBackend()
    for contract in _active_contracts():
        capability_id = contract.capability_id
        request = f"请严格使用 {capability_id} 生成仿真"
        assert plan_capability_for_request(request).selected_capability_id == capability_id
        assert backend.select_template(request, {}).capability_id == capability_id


def test_every_active_default_event_validates_and_derives_the_correct_mode() -> None:
    total = 0
    for contract in _active_contracts():
        schema = capability_form_schema(contract.capability_id)
        for plural, expected_mode in (
            ("faults", "fault"),
            ("degradations", "degradation"),
            ("constraints", "constraint"),
        ):
            for item in schema.get("event_catalog", {}).get(plural, []):
                total += 1
                form = deepcopy(schema["default_form"])
                form["events"][plural] = [deepcopy(item["default_event"])]
                spec = normalize_form_task_spec(
                    form,
                    task_id=f"event_{contract.capability_id.replace('.', '_')}_{item['effect']}",
                )
                assert spec["model"]["target"]["mode"] == expected_mode
                validation = validate_task_spec(spec)
                assert validation.ok, {
                    "capability_id": contract.capability_id,
                    "effect": item["effect"],
                    "validation": validation.to_dict(),
                }
    # Keep the active event catalog count explicit so accidental contract
    # additions or removals remain reviewable.
    assert total == 189


def test_mode_effect_catalog_matches_operator_contract_for_all_active_capabilities() -> None:
    for contract in _active_contracts():
        modes = contract.data.get("modes") or {}
        for kind in ("fault", "degradation", "constraint"):
            declared = _mode_effects(modes.get(kind), kind)
            operator = {
                effect.effect_id
                for effect in contract.operator_contract.effects
                if effect.kind == kind and effect.effect_id != "nominal"
            }
            assert declared == operator, {
                "capability_id": contract.capability_id,
                "kind": kind,
                "modes_only": sorted(declared - operator),
                "operator_only": sorted(operator - declared),
            }


def test_registered_capability_execution_is_non_legacy_and_retains_evidence() -> None:
    spec = _canonical_default("component.battery.v1")
    reset_execution_composition_root_for_tests()
    result = execute_compiled_task(
        compile_task_spec(spec),
        task_spec=spec,
        write_dataset=False,
    )
    evidence = result.runtime_metadata["unified_execution"]
    assert evidence["adapter_key"] == REGISTERED_CAPABILITY_ADAPTER_KEY
    assert evidence["route"] == "registered_capability_adapter"
    assert evidence["legacy_mode"] is False
    assert evidence["legacy_bridge_called"] is False
    assert evidence["capability_adapter"] == "sat_sim.adapters.component_battery.BatteryAdapter"
    assert result.trace_rows


def test_representative_effects_change_physics_and_emit_registered_evidence() -> None:
    cases = (
        ("component.reaction_wheel.v1", "degradations", "friction_increase_pct", "label.degradation_active", "adcs.reaction_wheel.damping_nms_0"),
        ("subsystem.eps.basic.v1", "faults", "open_circuit", "label.fault_active", "eps.battery.applied_power_w"),
        ("subsystem.eps.basic.v1", "degradations", "pdu_efficiency_loss_pct", "label.degradation_active", "eps.pdu.effective_efficiency"),
        ("subsystem.thermal.basic_lumped.v1", "degradations", "radiator_degradation_factor", "label.degradation_active", "thermal.radiator.rejected_heat_w"),
        ("subsystem.comm.basic_ground_pass.v1", "degradations", "comm_tx_power_loss_pct", "label.degradation_active", "comm.link.margin_db"),
    )
    for capability_id, plural, effect_id, label_field, evidence_field in cases:
        schema = capability_form_schema(capability_id)
        nominal_form = deepcopy(schema["default_form"])
        _, nominal = _run_form(nominal_form, capability_id, "nominal")
        event = next(item for item in schema["event_catalog"][plural] if item["effect"] == effect_id)
        event_form = deepcopy(schema["default_form"])
        event_form["events"][plural] = [deepcopy(event["default_event"])]
        _, affected = _run_form(event_form, capability_id, effect_id)
        assert any(row.get(label_field) is True for row in affected.trace_rows)
        assert evidence_field in affected.trace_rows[0]
        nominal_values = [row.get(evidence_field) for row in nominal.trace_rows]
        affected_values = [row.get(evidence_field) for row in affected.trace_rows]
        assert nominal_values != affected_values
        execution = affected.runtime_metadata["unified_execution"]
        assert execution["legacy_mode"] is False
        assert execution["legacy_bridge_called"] is False


def test_unknown_effect_fails_closed() -> None:
    schema = capability_form_schema("component.battery.v1")
    form = deepcopy(schema["default_form"])
    form["events"]["faults"] = [{
        "id": "unknown_effect_1",
        "event_type": "fault",
        "target": "battery",
        "effect": "unknown_battery_effect",
        "start_s": 0.0,
        "implementation": "auto",
        "delivery": "modifier",
        "parameters": {},
        "magnitude": 1.0,
    }]
    spec = normalize_form_task_spec(form, task_id="unknown_effect_fail_closed")
    validation = validate_task_spec(spec)
    assert validation.ok is False
    assert any("unknown_battery_effect" in issue.message for issue in validation.issues)


def test_maneuver_default_burn_window_is_inside_simulation_duration() -> None:
    form = capability_form_schema("whole_spacecraft.maneuver_orbit_attitude.v1")["default_form"]
    duration = float(form["simulation"]["duration_s"])
    values = form["parameters"]["values"]
    assert float(values["burn_start_s"]) < duration
    assert float(values["burn_start_s"]) + float(values["burn_duration_s"]) <= duration
