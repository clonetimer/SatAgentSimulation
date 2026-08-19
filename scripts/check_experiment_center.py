#!/usr/bin/env python3
"""
用途：执行实验中心模板、参数扫描、断言与 API 生命周期审计。
参数：无命令行参数；脚本内部使用临时目录。
输出：生成实验中心审计 JSON。
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.assertions import evaluate_assertions
from sat_sim.form_schema import capability_form_schema
from sat_sim.scenario_templates import instantiate_scenario_template, list_scenario_templates


def run(output: str | Path | None = None) -> dict:
    checks = []
    catalog = list_scenario_templates()
    checks.append({"name": "template_count", "pass": catalog["count"] >= 12, "value": catalog["count"]})
    spec = instantiate_scenario_template("whole_rw_jam", task_id="scenario_audit_rw")
    checks.append({"name": "template_fault_binding", "pass": spec["events"]["faults"][0]["effect"] == "adcs_rw_jamming"})
    assertion = evaluate_assertions({"score": 0.8}, [{"metric": "score", "operator": ">=", "value": 0.7}])
    checks.append({"name": "assertion_engine", "pass": assertion["status"] == "PASS"})

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        app = create_app(runs_root=root / "runs", artifacts_root=root / "artifacts", queue_database=root / "queue.sqlite3", embedded_worker=True)
        with TestClient(app) as client:
            form = capability_form_schema("component.battery.v1")["default_form"]
            form["task"]["id"] = "scenario_battery_experiment"
            form["simulation"].update({"duration_s": 20.0, "sample_s": 5.0, "step_s": 5.0})
            parsed = client.post("/tasks/parse", json={"input_kind": "form", "form_data": form, "compile_if_valid": True})
            task_spec = parsed.json()["result"]["task_spec"]
            created = client.post("/experiments", json={
                "name": "电池 SOC 扫描",
                "base_task_spec": task_spec,
                "sweep": {"parameters.values.initial_soc": [0.5, 0.8]},
                "assertions": [{"metric": "qoi.eps.battery.final_soc", "operator": ">=", "value": 0.0}],
            })
            experiment_id = created.json()["experiment"]["experiment_id"]
            launch = client.post(f"/experiments/{experiment_id}/launch", json={})
            checks.append({"name": "experiment_launch_count", "pass": launch.json().get("launched_count") == 2})
            detail = None
            for _ in range(200):
                detail = client.get(f"/experiments/{experiment_id}").json()
                states = [item["state"] for item in detail["members"]]
                if states and all(item in {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"} for item in states):
                    break
                time.sleep(0.05)
            members = detail["members"] if detail else []
            checks.append({"name": "experiment_runs_succeeded", "pass": len(members) == 2 and all(item["state"] == "SUCCEEDED" for item in members), "members": members})
            checks.append({"name": "experiment_assertions_pass", "pass": len(members) == 2 and all(item["assertion_status"] == "PASS" for item in members)})
            comparison = client.get(f"/experiments/{experiment_id}/comparison?metrics=qoi.eps.battery.final_soc").json()["comparison"]
            values = [row["values"].get("qoi.eps.battery.final_soc") for row in comparison["rows"]]
            checks.append({"name": "comparison_metric", "pass": len(values) == 2 and all(isinstance(item, (int, float)) for item in values), "values": values})
            health = client.get("/health").json()
            checks.append({"name": "experiment_health", "pass": health["experiments"]["experiment_count"] == 1})

    report = {
        "schema_version": "experiment-audit.v1",
        "status": "PASS" if all(item["pass"] for item in checks) else "FAIL",
        "check_count": len(checks),
        "pass_count": sum(bool(item["pass"]) for item in checks),
        "checks": checks,
    }
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    report = run("reports/scenario_experiment_center_audit.json")
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if report["status"] == "PASS" else 1)
