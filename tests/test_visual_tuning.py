from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from sat_sim.api import create_app
from sat_sim.visual_tuning import build_tuning_plan, rank_tuning_rows, tuning_options


def _spec() -> dict:
    return json.loads(Path("tests/fixtures/phase3g_roundtrip/nominal/task_spec.json").read_text(encoding="utf-8"))


def _client(tmp_path: Path) -> tuple[TestClient, object]:
    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    )
    return TestClient(app), app


def test_tuning_options_are_schema_registered_numeric_model_parameters() -> None:
    payload = tuning_options(_spec())
    assert payload["schema_version"] == "sat-sim.visual-tuning.v1"
    assert payload["limits"]["max_parameters"] == 3
    assert payload["limits"]["max_variants"] == 12
    paths = {item["path"] for item in payload["parameters"]}
    assert "parameters.values.control_kp_nm_per_rad" in paths
    assert "parameters.values.control_kd_nm_per_rad_s" in paths
    assert not any(path.startswith("simulation.") for path in paths)
    assert "qoi.adcs.final_pointing_error_deg" in {item["metric"] for item in payload["objectives"]}
    assert payload["arbitrary_parameter_paths_accepted"] is False


def test_tuning_plan_limits_grid_and_validates_registered_bounds() -> None:
    plan = build_tuning_plan(
        _spec(),
        {
            "parameters.values.control_kp_nm_per_rad": [0.08, 0.10, 0.12],
            "parameters.values.control_kd_nm_per_rad_s": [0.7, 0.9, 1.1],
        },
        objective_metric="qoi.adcs.final_pointing_error_deg",
        direction="minimize",
    )
    assert plan["parameter_count"] == 2
    assert plan["variant_count"] == 9
    assert len(plan["preview"]) == 9
    with pytest.raises(ValueError, match="unsupported tuning parameter path"):
        build_tuning_plan(
            _spec(),
            {"metadata.injected_python": [1, 2]},
            objective_metric="qoi.adcs.final_pointing_error_deg",
            direction="minimize",
        )
    with pytest.raises(ValueError, match="limit is 12"):
        build_tuning_plan(
            _spec(),
            {
                "parameters.values.control_kp_nm_per_rad": [0.06, 0.08, 0.10, 0.12],
                "parameters.values.control_kd_nm_per_rad_s": [0.6, 0.8, 1.0, 1.2],
            },
            objective_metric="qoi.adcs.final_pointing_error_deg",
            direction="minimize",
        )


def test_rank_tuning_rows_respects_direction() -> None:
    rows = [
        {"run_id": "a", "objective_value": 3.0, "parameters": {"p": 1}},
        {"run_id": "b", "objective_value": 1.0, "parameters": {"p": 2}},
        {"run_id": "c", "objective_value": 2.0, "parameters": {"p": 3}},
    ]
    minimized = rank_tuning_rows(rows, objective_metric="qoi.x", direction="minimize")
    maximized = rank_tuning_rows(rows, objective_metric="qoi.x", direction="maximize")
    assert [row["run_id"] for row in minimized] == ["b", "c", "a"]
    assert [row["run_id"] for row in maximized] == ["a", "c", "b"]
    assert minimized[0]["is_best"] is True


def test_visual_tuning_api_plans_and_ranks_existing_experiment_runs(tmp_path: Path) -> None:
    client, app = _client(tmp_path)
    spec = _spec()
    with client:
        options_response = client.post("/visual-composer/tuning-options", json={"task_spec": spec})
        assert options_response.status_code == 200
        options = options_response.json()["tuning"]
        assert options["parameter_count"] > 0

        plan_response = client.post(
            "/visual-composer/tuning-plan",
            json={
                "task_spec": spec,
                "parameters": {"parameters.values.control_kp_nm_per_rad": [0.08, 0.12]},
                "objective_metric": "qoi.adcs.final_pointing_error_deg",
                "direction": "minimize",
            },
        )
        assert plan_response.status_code == 200
        assert plan_response.json()["plan"]["variant_count"] == 2

        create_response = client.post(
            "/experiments",
            json={
                "name": "visual tuning api test",
                "base_task_spec": spec,
                "sweep": {"parameters.values.control_kp_nm_per_rad": [0.08, 0.12]},
                "experiment_type": "sweep",
            },
        )
        assert create_response.status_code == 200
        experiment_id = create_response.json()["experiment"]["experiment_id"]
        store = app.state.experiment_store
        for index, value in enumerate([5.0, 2.0]):
            run_id = f"tune-{index}"
            root = tmp_path / "runs" / run_id
            (root / "results").mkdir(parents=True)
            (root / "validation").mkdir(parents=True)
            (root / "run_record.json").write_text(json.dumps({"status": "SUCCEEDED", "validation_result": "PASS"}), encoding="utf-8")
            (root / "validation" / "validation_outcome.json").write_text(json.dumps({"result": "PASS"}), encoding="utf-8")
            (root / "results" / "metrics.json").write_text(json.dumps({"metrics": {"qoi.adcs.final_pointing_error_deg": value}}), encoding="utf-8")
            store.assign_run(experiment_id, index, run_id, state="SUCCEEDED")

        rank_response = client.post(
            "/visual-composer/tuning-rank",
            json={
                "experiment_id": experiment_id,
                "objective_metric": "qoi.adcs.final_pointing_error_deg",
                "direction": "minimize",
            },
        )
        assert rank_response.status_code == 200
        ranking = rank_response.json()
        assert ranking["complete"] is True
        assert ranking["best"]["run_id"] == "tune-1"
        assert ranking["best"]["objective_value"] == 2.0
        assert ranking["best"]["parameters"]["parameters.values.control_kp_nm_per_rad"] == 0.12
