from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from sat_sim.adapters.whole_spacecraft_composite_digital_twin import (
    WholeSpacecraftCompositeDigitalTwinAdapter,
)
from sat_sim.coupling_causality import CausalExpectation, evaluate_coupling_causality
from sat_sim.coupling_validation_suite import (
    _portable_environment_payload,
    run_coupling_validation_suite,
    validate_one_factor_change,
)
from sat_sim.execution_planner import plan_task_spec
from whole_spacecraft.task_cadence import evaluate_task_cadence, validate_task_periods


def _baseline_spec() -> dict:
    return yaml.safe_load(
        Path("configs/verification/whole_spacecraft_coupling_baseline.yaml").read_text(encoding="utf-8")
    )


def _matrix_payload(baseline: Path, *, case_id: str = "case_001") -> dict:
    return {
        "schema_version": "v0572a.coupling-validation-matrix.v1",
        "baseline_spec": str(baseline),
        "case_run_timeout_s": 30.0,
        "cases": [
            {
                "case_id": case_id,
                "description": "test",
                "patch": {"path": "parameters.payload_power_w", "value": 60.0},
                "expectations": [
                    {
                        "coupling_id": "payload_to_eps",
                        "source_field": "net_power_w",
                        "response_field": "battery_soc",
                        "expected_direction": "decrease",
                        "min_source_delta": 1.0,
                        "min_response_delta": 0.0001,
                    }
                ],
            }
        ],
    }


def test_task_period_grid_accepts_integer_multiples_and_rejects_semantic_aliasing() -> None:
    valid = validate_task_periods(
        dynamics_step_s=0.2,
        fsw_step_s=0.2,
        orbit_environment_step_s=1.0,
        thermal_step_s=10.0,
        recorder_step_s=0.4,
    )
    assert valid["status"] == "PASS"

    faster_output = validate_task_periods(
        dynamics_step_s=0.2,
        fsw_step_s=0.2,
        orbit_environment_step_s=1.0,
        thermal_step_s=10.0,
        recorder_step_s=0.1,
    )
    assert faster_output["status"] == "FAIL"
    assert faster_output["issues"][0]["reason_code"] == "TASK_PERIOD_FASTER_THAN_DYNAMICS"

    independent = validate_task_periods(
        dynamics_step_s=0.2,
        fsw_step_s=0.2,
        orbit_environment_step_s=1.0,
        thermal_step_s=10.0,
        recorder_step_s=0.5,
    )
    assert independent["status"] == "PASS"
    assert independent["scheduler_quantum_s"] == pytest.approx(0.1)

    non_representable = validate_task_periods(
        dynamics_step_s=0.2,
        fsw_step_s=0.2,
        orbit_environment_step_s=1.0,
        thermal_step_s=10.0,
        recorder_step_s=1.0 / 3.0,
    )
    assert non_representable["status"] == "FAIL"
    assert non_representable["issues"][0]["reason_code"] == "TASK_PERIOD_NOT_NANOSECOND_REPRESENTABLE"


def test_runtime_task_cadence_evidence_uses_actual_scheduler_activations() -> None:
    probes = {
        "dynamics": SimpleNamespace(configured_period_s=0.2, update_times_ns=[0, 200_000_000, 400_000_000, 600_000_000, 800_000_000, 1_000_000_000]),
        "thermal": SimpleNamespace(configured_period_s=1.0, update_times_ns=[0, 1_000_000_000]),
    }
    report = evaluate_task_cadence(probes, duration_s=1.0)
    assert report["status"] == "PASS"
    assert report["pass_count"] == 2
    assert report["checks"][0]["observed_count"] == 6

    probes["thermal"] = SimpleNamespace(configured_period_s=1.0, update_times_ns=[0])
    failed = evaluate_task_cadence(probes, duration_s=1.0)
    assert failed["status"] == "FAIL"
    thermal = next(item for item in failed["checks"] if item["task_name"] == "thermal")
    assert "TASK_EXECUTION_COUNT_MISMATCH" in thermal["reason_codes"]


def test_composite_adapter_rejects_output_sampling_that_changes_claimed_physical_grid() -> None:
    adapter = WholeSpacecraftCompositeDigitalTwinAdapter()

    too_fast = _baseline_spec()
    too_fast["simulation"]["sample_s"] = 0.1
    issues = adapter.validate(too_fast)
    assert any(item.code == "task_cadence" and "FASTER_THAN_DYNAMICS" in item.message for item in issues)

    independent = _baseline_spec()
    independent["simulation"]["sample_s"] = 0.5
    assert not [item for item in adapter.validate(independent) if item.code == "task_cadence"]

    non_representable = _baseline_spec()
    non_representable["simulation"]["sample_s"] = 1.0 / 3.0
    issues = adapter.validate(non_representable)
    assert any(item.code == "task_cadence" and "NANOSECOND_REPRESENTABLE" in item.message for item in issues)


def test_one_factor_validation_allows_declared_semantic_companion_only() -> None:
    baseline = {
        "target": {"mode": "nominal"},
        "parameters": {"payload_power_w": 38.0},
        "modifiers": {"faults": []},
    }
    perturbed = {
        "target": {"mode": "fault"},
        "parameters": {"payload_power_w": 38.0},
        "modifiers": {"faults": [{"fault_type": "instrument_off"}]},
    }
    report = validate_one_factor_change(
        baseline,
        perturbed,
        patch_path="modifiers.faults",
        companion_patch_paths=("target.mode",),
    )
    assert report["status"] == "PASS"

    contaminated = json.loads(json.dumps(perturbed))
    contaminated["parameters"]["payload_power_w"] = 60.0
    report = validate_one_factor_change(
        baseline,
        contaminated,
        patch_path="modifiers.faults",
        companion_patch_paths=("target.mode",),
    )
    assert report["status"] == "FAIL"
    assert "parameters.payload_power_w" in report["unexpected_differences"]


def test_causal_evaluator_requires_pre_equivalence_and_sustained_post_response() -> None:
    baseline = [
        {"time_s": 0.0, "source": 1.0, "sink": 10.0},
        {"time_s": 1.0, "source": 1.0, "sink": 10.0},
        {"time_s": 2.0, "source": 1.0, "sink": 10.0},
        {"time_s": 3.0, "source": 1.0, "sink": 10.0},
        {"time_s": 4.0, "source": 1.0, "sink": 10.0},
    ]
    perturbed = [
        {"time_s": 0.0, "source": 1.0, "sink": 10.0},
        {"time_s": 1.0, "source": 1.0, "sink": 10.0},
        {"time_s": 2.0, "source": 2.0, "sink": 11.0},
        {"time_s": 3.0, "source": 2.0, "sink": 12.0},
        {"time_s": 4.0, "source": 2.0, "sink": 12.0},
    ]
    expectation = CausalExpectation(
        coupling_id="source_to_sink",
        source_field="source",
        response_field="sink",
        intervention_time_s=2.0,
        expected_direction="increase",
        min_source_delta=0.5,
        min_response_delta=0.5,
        min_pre_samples=2,
        min_consecutive_response_samples=2,
        min_response_fraction=2 / 3,
    )
    passed = evaluate_coupling_causality(baseline, perturbed, [expectation])
    assert passed["status"] == "PASS"
    assert passed["checks"][0]["pre_equivalent"] is True
    assert passed["checks"][0]["max_consecutive_response_samples"] == 3

    contaminated = json.loads(json.dumps(perturbed))
    contaminated[0]["sink"] = 12.0
    failed = evaluate_coupling_causality(baseline, contaminated, [expectation])
    assert failed["status"] == "INCONCLUSIVE"
    assert "PRE_INTERVENTION_EQUIVALENCE_FAILED" in failed["checks"][0]["reason_codes"]


def test_suite_refuses_nonempty_output_directory_to_prevent_stale_bundle_reuse(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.yaml"
    baseline.write_text(yaml.safe_dump(_baseline_spec(), sort_keys=False), encoding="utf-8")
    matrix = tmp_path / "matrix.json"
    matrix.write_text(json.dumps(_matrix_payload(baseline)), encoding="utf-8")
    output = tmp_path / "suite"
    output.mkdir()
    (output / "stale-evidence.json").write_text("{}", encoding="utf-8")

    with pytest.raises(FileExistsError, match="must be new or empty"):
        run_coupling_validation_suite(matrix, output_root=output)


def test_suite_rejects_unsafe_or_duplicate_case_ids_before_execution(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.yaml"
    baseline.write_text(yaml.safe_dump(_baseline_spec(), sort_keys=False), encoding="utf-8")

    unsafe_matrix = tmp_path / "unsafe.json"
    unsafe_matrix.write_text(json.dumps(_matrix_payload(baseline, case_id="../escape")), encoding="utf-8")
    with pytest.raises(ValueError, match="unsafe coupling validation case_id"):
        run_coupling_validation_suite(unsafe_matrix, output_root=tmp_path / "unsafe-out")

    duplicate_payload = _matrix_payload(baseline)
    duplicate_payload["cases"].append(json.loads(json.dumps(duplicate_payload["cases"][0])))
    duplicate_matrix = tmp_path / "duplicate.json"
    duplicate_matrix.write_text(json.dumps(duplicate_payload), encoding="utf-8")
    with pytest.raises(ValueError, match="must be unique"):
        run_coupling_validation_suite(duplicate_matrix, output_root=tmp_path / "duplicate-out")


def test_runtime_fault_scenario_parameter_is_plannable() -> None:
    spec = _baseline_spec()
    spec["task_id"] = "v0572a_runtime_fault_planning"
    spec["target"]["mode"] = "fault"
    spec["modifiers"]["faults"] = [
        {
            "modifier_id": "payload_off_causal_001",
            "target": "payload.instrument",
            "fault_type": "instrument_off",
            "onset_time_s": 20.0,
            "duration_s": -1.0,
            "severity": 1.0,
            "parameters": {"scenario": "payload_instrument_off"},
        }
    ]
    planned = plan_task_spec(spec)
    assert planned.ok is True
    assert not any(issue.code == "EVENT_PARAMETER_UNKNOWN" for issue in planned.validation.issues)


def test_release_matrix_contains_four_declared_causal_cases() -> None:
    matrix = json.loads(
        Path("configs/verification/whole_spacecraft_coupling_causality_v1.json").read_text(encoding="utf-8")
    )
    case_ids = {item["case_id"] for item in matrix["cases"]}
    assert case_ids == {
        "payload_generation_to_storage",
        "payload_power_to_eps",
        "payload_power_to_thermal",
        "payload_fault_to_storage",
    }
    assert all(item["expectations"] for item in matrix["cases"])


def test_suite_evidence_reports_use_portable_paths() -> None:
    source = Path("src/sat_sim/coupling_validation_suite.py").read_text(encoding="utf-8")
    assert 'matrix_copy.relative_to(suite_root).as_posix()' in source
    assert 'baseline_copy.relative_to(suite_root).as_posix()' in source
    assert '_portable_execution_record' in source
    assert 'output directory must be new or empty' in source


def test_environment_doctor_payload_redacts_build_host_paths() -> None:
    payload = {
        "ok": True,
        "environment": {
            "cwd": "/tmp/build/project",
            "executable": "/tmp/build/.venv/bin/python",
        },
        "checks": [
            {
                "evidence": {
                    "path": "/tmp/build/project/third_party/spice/de440s.bsp",
                    "external": "/opt/vendor/data/kernel.tls",
                }
            }
        ],
    }
    portable = _portable_environment_payload(payload)
    serialized = json.dumps(portable, ensure_ascii=False)
    assert "/tmp/build" not in serialized
    assert "/opt/vendor" not in serialized
    assert "$PROJECT_ROOT" in serialized
    assert "$EXTERNAL/kernel.tls" in serialized
    assert portable["ok"] is True
    assert portable["path_redaction"]["status"] == "APPLIED"


def test_dependency_audit_script_accepts_external_report_paths() -> None:
    import importlib.util

    script = Path("scripts/scan_dependencies.py").resolve()
    spec = importlib.util.spec_from_file_location("sat_sim_scan_dependencies_v0572", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = Path.cwd().resolve()
    assert module._portable_path(root / "reports" / "audit.json", root) == "reports/audit.json"
    assert module._portable_path(Path("/tmp/external-audit.json"), root) == "$EXTERNAL/external-audit.json"
    assert module._portable_command_arg(str(root / "requirements-lock.txt"), root) == "<PROJECT_ROOT>/requirements-lock.txt"
    assert module._portable_command_arg("/tmp/external-audit.json", root) == "$EXTERNAL/external-audit.json"
