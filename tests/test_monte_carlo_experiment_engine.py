from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.experiment_manager import ExperimentStore
from sat_sim.experiments.monte_carlo_engine import expand_monte_carlo
from sat_sim.scenario_templates import instantiate_scenario_template


def test_monte_carlo_expansion_is_seed_reproducible():
    spec = instantiate_scenario_template("whole_nominal", task_id="mc_base")
    dispersions = {"simulation.duration_s": {"distribution": "uniform", "min": 10.0, "max": 20.0}}
    first = expand_monte_carlo(spec, dispersions, sample_count=4, seed=42)
    second = expand_monte_carlo(spec, dispersions, sample_count=4, seed=42)
    assert [item["parameters"] for item in first] == [item["parameters"] for item in second]
    assert len(first) == 4
    assert first[0]["task_spec"]["metadata"]["experiment_variant"]["experiment_type"] == "monte_carlo"
    assert first[0]["task_spec"]["metadata"]["experiment_variant"]["seed"] == 42


def test_experiment_store_creates_monte_carlo_records(tmp_path: Path):
    spec = instantiate_scenario_template("whole_nominal", task_id="mc_store_base")
    store = ExperimentStore(tmp_path / "experiments.sqlite3")
    record = store.create(
        name="时长不确定性 Monte Carlo",
        base_task_spec=spec,
        experiment_type="monte_carlo",
        sampling_plan={
            "seed": 7,
            "sample_count": 3,
            "dispersions": {"simulation.duration_s": {"distribution": "normal", "mean": 30.0, "std": 1.0}},
        },
    )
    assert record.experiment_type == "monte_carlo"
    assert record.variant_count == 3
    members = store.members(record.experiment_id)
    assert len(members) == 3
    assert members[0]["task_spec"]["metadata"]["experiment_variant"]["experiment_type"] == "monte_carlo"


def test_api_creates_monte_carlo_experiment_and_exposes_dispersion_options(tmp_path: Path):
    app = create_app(runs_root=tmp_path / "runs", artifacts_root=tmp_path / "artifacts", queue_database=tmp_path / "queue.sqlite3", embedded_worker=False)
    with TestClient(app) as client:
        spec = client.post("/scenario-templates/whole_nominal/instantiate", json={}).json()["task_spec"]
        options = client.post("/experiments/dispersion-options", json={"base_task_spec": spec})
        assert options.status_code == 200
        assert any(item["path"] == "simulation.duration_s" for item in options.json()["options"])
        created = client.post("/experiments", json={
            "name": "Monte Carlo 时长实验",
            "base_task_spec": spec,
            "experiment_type": "monte_carlo",
            "sampling_plan": {
                "seed": 11,
                "sample_count": 2,
                "dispersions": {"simulation.duration_s": {"distribution": "uniform", "min": 20, "max": 40}},
            },
        })
        assert created.status_code == 200, created.text
        assert created.json()["experiment"]["experiment_type"] == "monte_carlo"
        assert len(created.json()["members"]) == 2


def test_workbench_contains_monte_carlo_controls():
    root = Path(__file__).resolve().parents[1]
    html = (root / "src/sat_sim/web/index.html").read_text(encoding="utf-8")
    js = (root / "src/sat_sim/web/app.js").read_text(encoding="utf-8")
    assert "Monte Carlo" in html
    assert "experimentType" in html
    assert "/experiments/dispersion-options" in js
