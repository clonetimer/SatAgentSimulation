from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _module(root: Path):
    path = root / "src" / "sat_sim" / "reliability_soak.py"
    spec = importlib.util.spec_from_file_location("reliability_soak_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reliability_plan_has_1000_task_and_basilisk_recycle_coverage() -> None:
    root = Path(__file__).resolve().parents[1]
    plan = json.loads((root / "configs/reliability/r1_dc1_soak_plan.json").read_text())
    workloads = {item["workload_id"]: item for item in plan["workloads"]}
    thermal = workloads["thermal_long_lived_1000"]
    eps = workloads["eps_basilisk_recycled_100"]
    assert thermal["worker_count"] * thermal["cycles_per_worker"] == 1000
    assert eps["worker_count"] * eps["cycles_per_worker"] == 100
    assert thermal["concurrency"] <= 4
    assert plan["guardrails"]["minimum_success_rate"] == 1.0


def test_soak_content_hash_detects_mutation() -> None:
    module = _module(Path(__file__).resolve().parents[1])
    payload = {"status": "PASS", "successful_cycles": 10}
    payload["content_sha256"] = module._content_sha(payload)
    assert payload["content_sha256"] == module._content_sha(payload)
    payload["successful_cycles"] = 9
    assert payload["content_sha256"] != module._content_sha(payload)
