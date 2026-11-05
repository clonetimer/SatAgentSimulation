from __future__ import annotations

import json

import pytest
from pathlib import Path

from sat_sim.evidence_analysis import build_experiment_evidence


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _sealed_run(root: Path, run_id: str, *, final_soc: float, shed_count: int, event_count: int = 0):
    run = root / run_id
    _write(run / "run_record.json", {
        "run_id": run_id,
        "status": "SUCCEEDED",
        "validation_result": "PASS",
    })
    _write(run / "input" / "provenance.json", {
        "task_spec_sha256": f"task-{run_id}",
        "execution_plan_sha256": f"plan-{run_id}",
        "capability_id": "subsystem.eps.unified_native.v1",
    })
    _write(run / "results" / "metrics.json", {
        "metrics": {
            "final_soc": final_soc,
            "load_shed_event_count": shed_count,
        }
    })
    _write(run / "results" / "events.json", {
        "events": [{"event_id": f"event-{index}"} for index in range(event_count)]
    })
    _write(run / "results" / "assertions.json", {"status": "PASS"})
    _write(run / "validation" / "validation_outcome.json", {"result": "PASS"})
    _write(run / "SEALED.json", {"sealed": True})
    return run


def test_evidence_package_keeps_observations_and_deltas_traceable(tmp_path):
    _sealed_run(tmp_path, "exp_v001", final_soc=0.72, shed_count=1, event_count=1)
    _sealed_run(tmp_path, "exp_v002", final_soc=0.80, shed_count=0, event_count=0)

    members = [
        {
            "variant_index": 0,
            "run_id": "exp_v001",
            "parameters": {"parameters.values.battery_capacity_wh": 120},
            "state": "SUCCEEDED",
        },
        {
            "variant_index": 1,
            "run_id": "exp_v002",
            "parameters": {"parameters.values.battery_capacity_wh": 160},
            "state": "SUCCEEDED",
        },
    ]
    payload = build_experiment_evidence(
        experiment_id="exp",
        members=members,
        runs_root=tmp_path,
        requested_metrics=["final_soc", "load_shed_event_count"],
        integrity_checker=lambda root: {"ok": True, "bundle": root.name},
    )

    assert payload["complete"] is True
    assert payload["evidence_ready"] is True
    assert payload["terminal_count"] == 2
    assert payload["sealed_count"] == 2
    assert payload["runs"][0]["event_count"] == 1
    assert payload["runs"][0]["artifact_refs"]["metrics"].endswith("results/metrics.json")
    assert payload["runs"][0]["provenance"]["capability_id"] == "subsystem.eps.unified_native.v1"

    comparison = payload["comparisons"][0]
    assert comparison["baseline_variant_index"] == 0
    assert comparison["variant_index"] == 1
    assert comparison["metric_deltas"]["final_soc"] == pytest.approx(0.08)
    assert comparison["metric_deltas"]["load_shed_event_count"] == -1.0
    assert "Causal attribution" in comparison["interpretation_boundary"]


def test_evidence_package_does_not_upgrade_missing_or_unsealed_runs(tmp_path):
    run = tmp_path / "run_unsealed"
    _write(run / "run_record.json", {
        "run_id": "run_unsealed",
        "status": "SUCCEEDED",
        "validation_result": "PASS",
    })

    payload = build_experiment_evidence(
        experiment_id="exp_missing",
        members=[
            {
                "variant_index": 0,
                "run_id": "run_unsealed",
                "parameters": {},
                "state": "SUCCEEDED",
            },
            {
                "variant_index": 1,
                "run_id": None,
                "parameters": {},
                "state": "DRAFT",
            },
        ],
        runs_root=tmp_path,
    )

    codes = {item["code"] for item in payload["warnings"]}
    assert payload["complete"] is False
    assert payload["evidence_ready"] is False
    assert "TERMINAL_RUN_NOT_SEALED" in codes
    assert "METRICS_NOT_AVAILABLE" in codes
    assert "RUN_NOT_ASSIGNED" in codes
    assert payload["claim_policy"]["missing_evidence"].startswith("Must remain missing")


def test_evidence_package_rejects_run_id_path_escape(tmp_path):
    try:
        build_experiment_evidence(
            experiment_id="exp_escape",
            members=[{
                "variant_index": 0,
                "run_id": "../outside",
                "parameters": {},
                "state": "SUCCEEDED",
            }],
            runs_root=tmp_path,
        )
    except ValueError as exc:
        assert "escapes runs root" in str(exc)
    else:
        raise AssertionError("path escape must be rejected")


def test_evidence_ready_requires_integrity_when_checker_is_supplied(tmp_path):
    _sealed_run(tmp_path, "bad_integrity", final_soc=0.75, shed_count=0)
    payload = build_experiment_evidence(
        experiment_id="exp_integrity",
        members=[{
            "variant_index": 0,
            "run_id": "bad_integrity",
            "parameters": {},
            "state": "SUCCEEDED",
        }],
        runs_root=tmp_path,
        integrity_checker=lambda _root: {"ok": False, "reason_code": "HASH_MISMATCH"},
    )
    assert payload["complete"] is True
    assert payload["evidence_ready"] is False
    assert payload["runs"][0]["integrity"]["ok"] is False


def test_agent_evidence_api_returns_traceable_artifacts_without_upgrading_bad_integrity(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from sat_sim.api import create_app
    from sat_sim.scenario_templates import instantiate_scenario_template

    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        queue_database=tmp_path / "queue.sqlite",
        embedded_worker=False,
    )
    client = TestClient(app)

    tools = client.get("/agent/tools")
    assert tools.status_code == 200
    evidence_tool = next(
        item
        for item in tools.json()["experiment_tools"]["tools"]
        if item["name"] == "get_experiment_evidence"
    )
    assert evidence_tool["causal_claims_verified"] is False

    created = client.post(
        "/agent/experiments",
        json={
            "operation_id": "evidence:create",
            "name": "evidence api",
            "base_task_spec": instantiate_scenario_template("adcs_pointing"),
            "sweep": {"simulation.duration_s": [10]},
        },
    )
    assert created.status_code == 200
    experiment_id = created.json()["experiment_id"]
    run_id = f"{experiment_id}_v001"
    app.state.experiment_store.assign_run(experiment_id, 0, run_id, state="SUCCEEDED")

    root = tmp_path / "runs" / run_id
    _write(root / "run_record.json", {
        "run_id": run_id,
        "status": "SUCCEEDED",
        "validation_result": "PASS",
    })
    _write(root / "results" / "metrics.json", {
        "metrics": {"final_pointing_error_deg": 0.12}
    })
    _write(root / "input" / "provenance.json", {
        "task_spec_sha256": "task-hash",
        "execution_plan_sha256": "plan-hash",
        "capability_id": "subsystem.adcs_unified_native.v1",
    })
    _write(root / "SEALED.json", {"sealed": True})

    response = client.get(
        f"/agent/experiments/{experiment_id}/evidence",
        params={"metrics": "final_pointing_error_deg"},
    )
    assert response.status_code == 200
    evidence = response.json()["evidence"]
    assert evidence["runs"][0]["metrics"]["final_pointing_error_deg"] == 0.12
    assert evidence["runs"][0]["artifact_refs"]["metrics"].endswith("results/metrics.json")
    assert evidence["runs"][0]["provenance"]["execution_plan_sha256"] == "plan-hash"
    assert evidence["runs"][0]["integrity"]["ok"] is False
    assert evidence["evidence_ready"] is False
    assert evidence["claim_policy"]["causal_explanation"].startswith("Not verified")
