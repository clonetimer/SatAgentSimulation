from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle
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
            lambda spec: (
                spec["model"]["target"].update({"mode": "degradation"}),
                spec["events"]["degradations"].append({
                    "id": "rw_friction_increase", "target": "reaction_wheel",
                    "effect": "friction_increase_pct", "start_s": 1.0, "end_s": 2.0,
                    "parameters": {"friction_increase_pct": 10.0},
                }),
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


def test_native_rw_preflight_api_returns_actionable_validation_errors(tmp_path) -> None:
    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    )
    spec = deepcopy(_native_rw_spec("p9n4_native_preflight_api"))
    spec["simulation"]["sample_s"] = 1.1

    with TestClient(app) as client:
        response = client.post("/tasks/validate", json={"task_spec": spec})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is False, payload
    errors = payload["validation"]["errors"]
    assert errors, payload
    assert any(
        "sample_s" in str(item.get("path", "")) or "sample_s" in str(item.get("message", ""))
        for item in errors
    ), json.dumps(errors, ensure_ascii=False, indent=2)
    assert payload["task_spec"]["parameters"]["values"]["physics_backend"] == "basilisk_native"


def test_native_rw_result_report_and_export_contract_uses_sealed_run_bundle(tmp_path) -> None:
    """N4-B: user-facing results must resolve to the same immutable native Run."""
    runs_root = tmp_path / "runs"
    spec = deepcopy(_native_rw_spec("p9n4_native_result_contract"))
    spec["simulation"].update({"duration_s": 2.0, "step_s": 0.1, "sample_s": 0.1})
    spec["parameters"]["values"].update({
        "wheel_inertia_kg_m2": 0.1,
        "max_motor_torque_nm": 0.005,
        "max_speed_rad_s": 1000.0,
        "initial_wheel_speeds_rad_s": 0.0,
        "command_torque_nm": 0.05,
        "damping_nms": 0.0,
    })
    spec.setdefault("model", {}).setdefault("validation", {}).update({
        "physical_checks": [],
        "acceptance_assertions": [{
            "id": "native_rw_result_speed_response",
            "metric": "qoi.adcs.reaction_wheel.final_speed_rad_s_0",
            "operator": ">",
            "value": 0.02,
        }],
    })
    prepared = prepare_run(spec, output_root=runs_root)
    executed = execute_prepared_run(
        prepared.bundle_root,
        expected_plan_sha256=prepared.execution_plan_sha256,
        max_attempts=1,
        hard_timeout=True,
    )
    assert executed.run_record.status.value == "SUCCEEDED"
    assert executed.validation.result.value == "PASS"
    assert verify_run_bundle(executed.bundle_root)["ok"] is True

    app = create_app(
        runs_root=runs_root,
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    )
    run_id = executed.run_record.run_id
    with TestClient(app) as client:
        detail = client.get(f"/runs/{run_id}")
        report = client.get(f"/runs/{run_id}/report")
        metrics = client.get(f"/runs/{run_id}/metrics")
        plots = client.get(f"/runs/{run_id}/plots")
        telemetry = client.get(f"/runs/{run_id}/telemetry?limit=100")
        artifacts = client.get(f"/runs/{run_id}/artifacts")
        bundle_download = client.get(f"/runs/{run_id}/bundle/download")
        summary_path = runs_root / run_id / "results" / "summary.json"
        summary_path.write_bytes(summary_path.read_bytes() + b"\\n")
        tampered_download = client.get(f"/runs/{run_id}/bundle/download")

    for response in (detail, report, metrics, plots, telemetry, artifacts):
        assert response.status_code == 200, response.text
        assert response.json().get("run_id", run_id) == run_id

    run = detail.json()["run"]
    assert run["sealed"] is True
    assert run["run_record"]["status"] == "SUCCEEDED"
    assert run["summary"]["physics_backend"] == "basilisk_native"
    assert report.json()["summary"]["physics_backend"] == "basilisk_native"
    assert report.json()["integrity"]["ok"] is True
    assert metrics.json()["metrics"]
    assert plots.json()["plot_manifest"]
    assert telemetry.json()["rows"]
    artifact_paths = {item["path"] for item in artifacts.json()["artifacts"]}
    assert "runtime/execution_port.json" in artifact_paths

    assert bundle_download.status_code == 200, bundle_download.text
    assert tampered_download.status_code == 409, tampered_download.text
    assert tampered_download.json()["detail"]["reason_code"] == "RUN_BUNDLE_NOT_VERIFIED"
    assert bundle_download.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(bundle_download.content)) as archive:
        manifest_path = f"{run_id}/bundle_manifest.json"
        seal_path = f"{run_id}/SEALED.json"
        assert manifest_path in archive.namelist()
        assert seal_path in archive.namelist()
        manifest_bytes = archive.read(manifest_path)
        manifest = json.loads(manifest_bytes)
        seal = json.loads(archive.read(seal_path))
        assert manifest["run_id"] == run_id
        assert seal["run_id"] == run_id
        assert seal["bundle_manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
        archived = set(archive.namelist())
        expected = {f"{run_id}/{relative}" for relative in manifest["files"]}
        assert expected.issubset(archived)
        for relative, entry in manifest["files"].items():
            payload = archive.read(f"{run_id}/{relative}")
            assert hashlib.sha256(payload).hexdigest() == entry["sha256"]


def test_run_bundle_download_rejects_unsealed_bundle(tmp_path) -> None:
    runs_root = tmp_path / "runs"
    prepared = prepare_run(_native_rw_spec("p9n4_unsealed_export"), output_root=runs_root)
    app = create_app(
        runs_root=runs_root,
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    )
    with TestClient(app) as client:
        response = client.get(f"/runs/{prepared.run_id}/bundle/download")
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["reason_code"] == "RUN_BUNDLE_NOT_VERIFIED"
