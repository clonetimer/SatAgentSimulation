from __future__ import annotations

from copy import deepcopy

from sat_sim.task_compiler import compile_task_spec
from sat_sim.task_models import canonicalize_task_spec, to_runtime_task_spec
from sat_sim.task_validator import validate_task_spec


def _outputs() -> dict:
    return {
        "output_root": "runs",
        "trace_format": "csv",
        "include_summary": True,
        "include_trace": True,
        "include_labels": True,
        "include_manifest": True,
    }


def test_reference_level_migrates_and_compiles_through_capability_adapter() -> None:
    spec = {
        "schema_version": "0.1.0",
        "task_id": "reference_contract_smoke",
        "task_type": "reference",
        "capability_id": "reference.public_satellite_case.v1",
        "simulation": {"duration_s": 1.0, "sample_s": 1.0, "backend": "python"},
        "target": {"level": "reference", "name": "public_satellite_case", "mode": "nominal"},
        "parameters": {"case_id": "public_1u_cubesat_thermal_power_orbit"},
        "outputs": _outputs(),
    }

    canonical = canonicalize_task_spec(spec)
    assert canonical["simulation"]["level"] == "reference"
    assert canonical["model"]["target"]["level"] == "reference"

    runtime = to_runtime_task_spec(canonical)
    assert runtime["task_type"] == "reference"
    compiled = compile_task_spec(spec, validate=False)
    assert compiled.task_type == "reference"
    assert compiled.runner == "sat_sim.adapters.reference_public_satellite_case.PublicSatelliteReferenceCaseAdapter"


def test_constraint_mode_is_a_first_class_mode_and_requires_constraint_event() -> None:
    spec = {
        "schema_version": "0.1.0",
        "task_id": "constraint_contract_smoke",
        "task_type": "whole_spacecraft",
        "capability_id": "whole_spacecraft.bsksim_foundation.v1",
        "simulation": {"duration_s": 10.0, "sample_s": 1.0, "backend": "basilisk", "solver": {"step_s": 1.0}},
        "target": {"level": "whole_spacecraft", "name": "whole_spacecraft", "mode": "constraint"},
        "spacecraft": {"mission": {"template": "constraint_contract_smoke"}},
        "parameters": {},
        "modifiers": {
            "faults": [],
            "degradations": [],
            "constraints": [
                {
                    "constraint_id": "rw_limit",
                    "target": "reaction_wheel",
                    "constraint_type": "reaction_wheel_speed_limit",
                    "onset_time_s": 0.0,
                    "duration_s": -1.0,
                    "parameters": {"max_speed_rad_s": 500.0},
                }
            ],
        },
        "outputs": _outputs(),
    }

    result = validate_task_spec(spec)
    assert not result.errors, [issue.to_dict() for issue in result.errors]
    canonical = canonicalize_task_spec(spec)
    assert canonical["model"]["target"]["mode"] == "constraint"
    assert canonical["events"]["constraints"][0]["effect"] == "reaction_wheel_speed_limit"

    missing_event = deepcopy(spec)
    missing_event["modifiers"]["constraints"] = []
    missing_result = validate_task_spec(missing_event)
    assert any(issue.code in {"TASKSPEC_MODEL_INVALID", "mode_contract"} for issue in missing_result.errors)


def test_capability_target_name_is_not_rejected_by_generic_catalog_and_bad_name_fails_capability() -> None:
    spec = {
        "schema_version": "0.1.0",
        "task_id": "specialized_target_contract_smoke",
        "task_type": "subsystem",
        "capability_id": "subsystem.thermal_reduced_order.v1",
        "simulation": {"duration_s": 10.0, "sample_s": 1.0, "backend": "python"},
        "target": {"level": "subsystem", "name": "thermal_reduced_order", "mode": "nominal"},
        "parameters": {},
        "outputs": _outputs(),
    }

    valid_result = validate_task_spec(spec)
    assert not any(issue.code == "target" and issue.path == "$.target.name" for issue in valid_result.errors)

    bad = deepcopy(spec)
    bad["target"]["name"] = "not_a_thermal_target"
    bad_result = validate_task_spec(bad)
    assert any(issue.path == "$.target.name" and issue.code == "capability" for issue in bad_result.errors)
