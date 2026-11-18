from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.scenario_templates import instantiate_scenario_template
from sat_sim.task_validator import validate_task_spec


def _native_rw_spec(task_id: str = "p9n4_native_preflight"):
    return instantiate_scenario_template(
        "component_reaction_wheel_basilisk_native",
        task_id=task_id,
    )


@pytest.mark.parametrize(
    ("mutate", "expected_path_fragment"),
    [
        (
            lambda spec: spec["events"]["faults"].append(
                {"id": "unsupported_native_fault", "effect": "rw_jamming", "target": "reaction_wheel",
                 "start_s": 1.0, "end_s": 2.0, "parameters": {}}
            ),
            "physics_backend",
        ),
        (
            lambda spec: spec.update(
                {"degradations": {"adcs": {"reaction_wheel": {"friction_increase_pct": 10.0}}}}
            ),
            "physics_backend",
        ),
        (
            lambda spec: spec["parameters"]["values"].update({"torque_profile_nm": [0.0, 0.001, 0.0]}),
            "physics_backend",
        ),
        (
            lambda spec: spec["simulation"].update({"sample_s": 1.1}),
            "sample_s",
        ),
        (
            lambda spec: spec["simulation"].update({"duration_s": 121.0}),
            "duration_s",
        ),
    ],
    ids=("fault-event", "degradation-event", "time-varying-torque", "sample-limit", "duration-limit"),
)
def test_native_rw_preflight_rejects_unsupported_configuration_with_field_path(
    mutate, expected_path_fragment: str
) -> None:
    spec = deepcopy(_native_rw_spec())
    mutate(spec)

    result = validate_task_spec(spec)

    assert not result.ok, result.to_dict()
    assert any(
        expected_path_fragment in issue.path
        for issue in result.errors
    ), result.to_dict()
    assert any(issue.code == "native_scope" for issue in result.errors), result.to_dict()


def test_native_rw_preflight_compile_api_returns_actionable_validation_errors(tmp_path) -> None:
    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    )
    spec = deepcopy(_native_rw_spec("p9n4_native_preflight_api"))
    spec["simulation"]["sample_s"] = 1.1

    with TestClient(app) as client:
        response = client.post(
            "/tasks/parse",
            json={"input_kind": "task_spec", "task_spec": spec, "compile_if_valid": True},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is False, payload
    errors = payload["result"]["validation"]["errors"]
    assert errors, payload
    assert any(
        item["code"] == "native_scope"
        and "sample_s" in item["path"]
        and "1.0" in item["message"]
        for item in errors
    ), payload
    assert payload["result"]["task_spec"]["parameters"]["values"]["physics_backend"] == "basilisk_native"
