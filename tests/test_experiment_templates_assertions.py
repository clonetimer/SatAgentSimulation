from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.assertions import evaluate_assertions
from sat_sim.experiment_manager import ExperimentStore, expand_sweep
from sat_sim.scenario_templates import instantiate_scenario_template, list_scenario_templates


def test_chinese_templates_are_registry_backed_and_valid():
    catalog = list_scenario_templates()
    assert catalog["count"] >= 12
    names = {item["name"] for item in catalog["templates"]}
    assert "完整整星正常运行" in names
    assert "反作用轮卡滞" in names
    spec = instantiate_scenario_template("whole_rw_jam")
    assert spec["model"]["capability_id"] == "whole_spacecraft.composite_digital_twin.v1"
    assert spec["model"]["target"]["mode"] == "fault"
    assert spec["events"]["faults"][0]["effect"] == "adcs_rw_jamming"


def test_assertion_operators_and_missing_metric():
    result = evaluate_assertions(
        {"score": 0.82, "status": "PASS"},
        [
            {"metric": "score", "operator": ">=", "value": 0.8},
            {"metric": "score", "operator": "within", "value": [0.8, 0.9]},
            {"metric": "status", "operator": "==", "value": "PASS"},
            {"metric": "missing", "operator": "==", "value": 1},
        ],
    )
    assert result["status"] == "FAIL"
    assert result["pass_count"] == 3
    assert result["fail_count"] == 1


def test_experiment_store_expands_cartesian_product(tmp_path: Path):
    spec = instantiate_scenario_template("whole_nominal", task_id="exp_base")
    variants = expand_sweep(spec, {
        "simulation.duration_s": [20.0, 40.0],
        "parameters.values.initial_soc": [0.5, 0.7],
    })
    assert len(variants) == 4
    assert variants[0]["task_spec"]["parameters"]["values"]["initial_soc"] == 0.5
    assert variants[0]["task_spec"]["model"]["config"]["initial_soc"] == 0.5
    store = ExperimentStore(tmp_path / "experiments.sqlite3")
    record = store.create(
        name="SOC与时长扫描", base_task_spec=spec,
        sweep={"simulation.duration_s": [20.0, 40.0]},
        assertions=[{"metric": "status", "operator": "==", "value": "PASS"}],
    )
    assert record.variant_count == 2
    assert len(store.members(record.experiment_id)) == 2


def test_api_templates_experiment_and_comparison_contract(tmp_path: Path):
    app = create_app(
        runs_root=tmp_path / "runs", artifacts_root=tmp_path / "artifacts",
        queue_database=tmp_path / "queue.sqlite3", embedded_worker=False,
    )
    with TestClient(app) as client:
        catalog = client.get("/scenario-templates")
        assert catalog.status_code == 200
        assert catalog.json()["count"] >= 12
        instantiated = client.post("/scenario-templates/whole_nominal/instantiate", json={})
        assert instantiated.status_code == 200
        spec = instantiated.json()["task_spec"]
        created = client.post("/experiments", json={
            "name": "时长扫描", "base_task_spec": spec,
            "sweep": {"simulation.duration_s": [20, 40]},
            "assertions": [{"metric": "status", "operator": "==", "value": "PASS"}],
        })
        assert created.status_code == 200
        experiment_id = created.json()["experiment"]["experiment_id"]
        detail = client.get(f"/experiments/{experiment_id}")
        assert detail.status_code == 200
        assert len(detail.json()["members"]) == 2
        launch = client.post(f"/experiments/{experiment_id}/launch", json={})
        assert launch.status_code == 200
        assert launch.json()["launched_count"] == 2
        health = client.get("/health").json()
        assert health["experiments"]["experiment_count"] == 1


def test_workbench_contains_scenario_controls():
    root = Path(__file__).resolve().parents[1]
    html = (root / "src/sat_sim/web/index.html").read_text(encoding="utf-8")
    js = (root / "src/sat_sim/web/app.js").read_text(encoding="utf-8")
    assert "实验中心" in html
    assert "scenarioTemplateSelect" in html
    assert "创建实验" in html
    assert "创建参数扫描实验" in js
    assert "创建 Monte Carlo 实验" in js
    assert "/scenario-templates" in js
    assert "/experiments" in js


def test_run_bundle_writes_assertion_evidence(tmp_path: Path):
    from sat_sim.run_bundle import execute_prepared_run, prepare_run
    from sat_sim.task_runner import TaskRunResult

    spec = instantiate_scenario_template("whole_nominal", task_id="assertion_bundle")
    prepared = prepare_run(spec, output_root=tmp_path / "runs", run_id="assertion_bundle")

    def fake_executor(compiled, _spec):
        return TaskRunResult(
            compiled=compiled,
            summary={
                "status": "PASS",
                "energy_conservation_status": "PASS",
                "data_conservation_status": "PASS",
            },
            trace_rows=({"time_s": 0.0, "status": 1.0}, {"time_s": 1.0, "status": 1.0}),
        )

    result = execute_prepared_run(
        prepared.bundle_root,
        expected_plan_sha256=prepared.execution_plan_sha256,
        executor=fake_executor,
    )
    assert result.run_record.status.value == "SUCCEEDED"
    assertion_file = Path(prepared.bundle_root) / "results" / "assertions.json"
    payload = json.loads(assertion_file.read_text(encoding="utf-8"))
    assert payload["status"] == "PASS"
    assert payload["pass_count"] == 3
