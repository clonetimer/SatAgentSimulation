from __future__ import annotations

import json
from pathlib import Path

from sat_sim.adapters.component_reaction_wheel import ReactionWheelAdapter
from sat_sim.adapters.subsystem_adcs_fidelity import AdcsFidelityAdapter
from sat_sim.capability_registry import get_capability
from sat_sim.form_schema import capability_form_schema
from sat_sim.reporting import generate_run_report
from sat_sim.task_models import CanonicalTaskSpec, to_runtime_task_spec


def _canonical_with_legacy_saturation(effect: str = "rw_speed_saturation") -> dict:
    return {
        "schema_version": "1.0.0",
        "task": {"id": "rw-constraint-compat", "name": "反作用轮速度限制兼容性测试"},
        "simulation": {"level": "component", "duration_s": 8.0, "step_s": 1.0, "sample_s": 1.0},
        "parameters": {"profile": "demo", "values": {"num_wheels": 1, "initial_wheel_speeds_rad_s": [50.0], "command_torque_nm": [0.01], "max_speed_rad_s": [100.0]}},
        "events": {
            "faults": [{
                "id": "legacy-saturation", "event_type": "fault", "target": "reaction_wheel_0",
                "effect": effect, "start_s": 2.0, "end_s": 6.0, "magnitude": 0.5,
                "implementation": "native", "delivery": "modifier", "parameters": {"wheel_index": 0, "speed_limit_scale": 0.5},
            }],
            "degradations": [],
        },
        "outputs": {"output_root": "runs", "plots": ["adcs.reaction_wheel.speed_rad_s_0"]},
        "assurance": {"parameter_profile": "demo"},
        "model": {"capability_id": "component.reaction_wheel.v1", "target": {"level": "component", "name": "reaction_wheel", "mode": "nominal"}},
    }


def test_legacy_saturation_fault_is_reclassified_as_constraint() -> None:
    model = CanonicalTaskSpec.model_validate(_canonical_with_legacy_saturation())
    assert model.events.faults == []
    assert len(model.events.constraints) == 1
    event = model.events.constraints[0]
    assert event.event_type == "constraint"
    assert event.effect == "reaction_wheel_speed_limit"
    assert event.delivery == "runtime_constraint"


def test_form_catalog_separates_faults_degradations_and_constraints() -> None:
    for capability_id, saturation in (
        ("component.reaction_wheel.v1", "reaction_wheel_speed_limit"),
        ("subsystem.adcs_fidelity.v1", "adcs_reaction_wheel_speed_limit"),
    ):
        schema = capability_form_schema(capability_id)
        catalog = schema["event_catalog"]
        assert saturation not in {item["effect"] for item in catalog["faults"]}
        assert saturation in {item["effect"] for item in catalog["constraints"]}
        constraint = next(item for item in catalog["constraints"] if item["effect"] == saturation)
        assert "故障" not in constraint["label"]
        assert "速度限制" in constraint["label"]


def test_operator_contract_classifies_saturation_as_constraint() -> None:
    for capability_id, saturation in (
        ("component.reaction_wheel.v1", "reaction_wheel_speed_limit"),
        ("subsystem.adcs_fidelity.v1", "adcs_reaction_wheel_speed_limit"),
    ):
        contract = get_capability(capability_id)
        effect = next(item for item in contract.operator_contract.effects if item.effect_id == saturation)
        assert effect.kind == "constraint"


def test_component_speed_limit_is_constraint_not_fault() -> None:
    canonical = CanonicalTaskSpec.model_validate(_canonical_with_legacy_saturation()).model_dump(mode="python", exclude_none=True)
    result = ReactionWheelAdapter().run(to_runtime_task_spec(canonical))
    active = [row for row in result.trace_rows if 2.0 <= float(row["time_s"]) < 6.0]
    assert active
    assert all(row["label.constraint_active"] is True for row in active)
    assert all(row["label.fault_active"] is False for row in active)
    assert all(str(row["label.health_state"]) == "nominal" for row in active)
    assert result.summary["events"]["fault_count"] == 0
    assert result.summary["events"]["constraint_count"] == 1
    assert all(float(row["adcs.reaction_wheel.effective_max_speed_rad_s_0"]) < float(row["adcs.reaction_wheel.nominal_max_speed_rad_s_0"]) for row in active)


def test_adcs_speed_limit_is_constraint_not_fault() -> None:
    spec = {
        "capability_id": "subsystem.adcs_fidelity.v1",
        "task_id": "adcs_constraint_test",
        "task_type": "subsystem",
        "target": {"level": "subsystem", "name": "adcs", "mode": "nominal"},
        "simulation": {"duration_s": 12.0, "sample_s": 2.0, "solver": {"step_s": 0.25}},
        "parameters": {"initial_attitude_error_deg": 12.0, "reaction_wheel_max_speed_rad_s": 900.0},
        "modifiers": {
            "faults": [], "degradations": [],
            "constraints": [{
                "constraint_id": "limit_1", "target": "adcs.reaction_wheel.0",
                "constraint_type": "adcs_reaction_wheel_speed_limit", "onset_time_s": 4.0,
                "duration_s": 4.0, "severity": 0.5,
                "parameters": {"wheel_index": 0, "speed_limit_scale": 0.5},
            }],
        },
    }
    result = AdcsFidelityAdapter().run(spec)
    active = [row for row in result.trace_rows if 4.0 <= float(row["time_s"]) < 8.0]
    assert active
    assert all(row["label.constraint_active"] is True for row in active)
    assert all(row["label.fault_active"] is False for row in active)
    assert result.summary["events"]["fault_count"] == 0
    assert result.summary["events"]["constraint_count"] == 1


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_report_uses_operational_constraint_category(tmp_path: Path) -> None:
    root = tmp_path / "run_constraint"
    _write_json(root / "input/task_spec.json", {"task": {"id": "t1", "name": "速度限制测试"}, "simulation": {"duration_s": 10}, "model": {"capability_id": "component.reaction_wheel.v1"}})
    _write_json(root / "run_record.json", {"status": "SUCCEEDED"})
    _write_json(root / "validation/validation_outcome.json", {"result": "PASS"})
    _write_json(root / "validation/claim_report.json", {"allowed_claims": []})
    _write_json(root / "results/metrics.json", {"metrics": {}})
    _write_json(root / "results/events.json", {"declared": [{"kind": "constraint", "constraint_type": "reaction_wheel_speed_limit", "target": "reaction_wheel_0", "start_time_s": 2.0}]})
    _write_json(root / "results/assertions.json", {"status": "PASS", "results": []})
    _write_json(root / "results/plot_manifest.json", {"series": []})
    _write_json(root / "runtime/environment.json", {})
    _write_json(root / "runtime/dependency_versions.json", {"dependencies": {}})
    generate_run_report(root)
    html = (root / "results/report.html").read_text(encoding="utf-8")
    assert "故障、退化与运行约束事件" in html
    assert "运行约束" in html
    assert "反作用轮达到速度限制" in html


def test_source_fault_enum_excludes_speed_limit() -> None:
    from components.reaction_wheel.constraints import RWConstraintType
    from components.reaction_wheel.faults import RWFaultType
    from sat_sim.task_validator import KNOWN_CONSTRAINT_TYPES, KNOWN_FAULT_TYPES

    assert not hasattr(RWFaultType, "SpeedSaturation")
    assert RWConstraintType.SPEED_LIMIT.value == "reaction_wheel_speed_limit"
    assert "reaction_wheel_speed_limit" in KNOWN_CONSTRAINT_TYPES
    assert "rw_speed_saturation" not in KNOWN_FAULT_TYPES


def test_component_reference_runner_has_separate_constraint_mode() -> None:
    from components.reaction_wheel.constraints import (
        RWConstraintType,
        ReactionWheelConstraintSpec,
    )
    from components.reaction_wheel.runner import run_constraint_case

    result = run_constraint_case([
        ReactionWheelConstraintSpec(
            constraint_type=RWConstraintType.SPEED_LIMIT,
            magnitude=0.5,
            target_id="rw_0",
        )
    ])
    assert result["mode"] == "constraint"
    assert result["health_state"] == "nominal"
    assert result["config"]["max_speed_rad_s"][0] < 1000.0


def test_effect_evidence_classifies_speed_limit_as_constraint() -> None:
    import json
    from importlib import resources

    payload = json.loads(
        resources.files("sat_sim.evidence")
        .joinpath("effect_evidence_v32.json")
        .read_text(encoding="utf-8")
    )
    entries = payload.get("effects", payload)
    item = next(entry for entry in entries if entry.get("effect_id") == "reaction_wheel_speed_limit")
    assert item["kind"] == "constraint"
    assert "label.constraint_active" in item["evidence_fields"]
    assert "label.fault_active" not in item["evidence_fields"]


def test_run_bundle_verifies_constraint_delivery_and_effect(tmp_path: Path) -> None:
    from sat_sim.run_bundle import execute_prepared_run, prepare_run

    canonical = CanonicalTaskSpec.model_validate(_canonical_with_legacy_saturation()).model_dump(
        mode="python", exclude_none=True
    )
    prepared = prepare_run(canonical, output_root=tmp_path / "runs", run_id="rw_constraint_bundle")
    result = execute_prepared_run(
        prepared.bundle_root,
        expected_plan_sha256=prepared.execution_plan_sha256,
    )
    assert result.run_record.status.value == "SUCCEEDED"
    assert result.run_record.validation_result == "PASS"
    evidence = result.validation.injection_evidence
    assert len(evidence) == 1
    assert evidence[0].event_kind == "constraint"
    assert evidence[0].registered is True
    assert evidence[0].active_sample_count > 0
    assert evidence[0].delivery_result.value == "PASS"
    assert evidence[0].effect_result.value == "PASS"
