"""Aggregate hash-backed vLLM evidence into a frozen latency/token baseline."""
from __future__ import annotations

import json
import statistics
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

from .task_spec import write_json

VLLM_BUDGETS = {
    "p95_latency_ms": 15_000.0,
    "max_latency_ms": 30_000.0,
    "p95_total_tokens": 3_000,
    "verified_rate": 1.0,
    "error_count": 0,
}


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return ordered[index]


def _service_status(base_url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(
            f"{base_url.rstrip('/')}/models",
            timeout=15.0,
        ) as response:  # nosec B310 - operator-supplied local OpenAI-compatible endpoint
            payload = json.loads(response.read(2_097_152).decode("utf-8"))
        models = [
            str(item.get("id"))
            for item in payload.get("data", [])
            if isinstance(item, dict) and item.get("id")
        ]
        return {"ok": bool(models), "models": models}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "models": []}


def _gpu_status() -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15.0,
        )
        rows = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        return {
            "ok": completed.returncode == 0 and bool(rows),
            "return_code": completed.returncode,
            "rows": rows,
            "error": completed.stderr.strip() or None,
        }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "rows": []}


def run_vllm_evidence_benchmark(
    *,
    reports_root: str | Path,
    output_path: str | Path,
    base_url: str = "http://127.0.0.1:8000/v1",
) -> dict[str, Any]:
    root = Path(reports_root)
    calls: dict[str, tuple[int, dict[str, Any]]] = {}
    source_files: dict[str, str] = {}
    for path in sorted(root.glob("vllm_nl_e2e_matrix_*/**/model_call*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        evidence = payload.get("invocation_evidence")
        if not isinstance(evidence, dict):
            continue
        case_id = path.parent.name
        modified = path.stat().st_mtime_ns
        current = calls.get(case_id)
        if case_id and (current is None or modified > current[0]):
            calls[case_id] = (modified, evidence)
            source_files[case_id] = str(path)
    if not calls:
        raise ValueError("no vLLM invocation evidence found")

    values = [item[1] for item in calls.values()]
    latencies = [float(item["latency_ms"]) for item in values]
    prompt_tokens = [int((item.get("usage") or {}).get("prompt_tokens") or 0) for item in values]
    completion_tokens = [
        int((item.get("usage") or {}).get("completion_tokens") or 0) for item in values
    ]
    total_tokens = [int((item.get("usage") or {}).get("total_tokens") or 0) for item in values]
    verified_count = sum(item.get("verified") is True for item in values)
    errors = [item for item in values if item.get("error")]
    p95_latency = _percentile(latencies, 0.95)
    p95_tokens = int(_percentile([float(item) for item in total_tokens], 0.95))
    service = _service_status(base_url)
    gpu = _gpu_status()
    checks = {
        "sample_count_at_least_279": len(values) >= 279,
        "verified_rate": verified_count / len(values) == VLLM_BUDGETS["verified_rate"],
        "error_count": len(errors) == VLLM_BUDGETS["error_count"],
        "p95_latency_budget": p95_latency <= VLLM_BUDGETS["p95_latency_ms"],
        "max_latency_budget": max(latencies) <= VLLM_BUDGETS["max_latency_ms"],
        "p95_token_budget": p95_tokens <= VLLM_BUDGETS["p95_total_tokens"],
        "service_online": bool(service["ok"]),
        "gpu_visible": bool(gpu["ok"]),
    }
    report = {
        "schema_version": "sat-sim.vllm-evidence-benchmark.v1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "budgets": VLLM_BUDGETS,
        "checks": checks,
        "sample_count": len(values),
        "selection_policy": "latest verified invocation per logical 31x3x3 case id",
        "verified_count": verified_count,
        "error_count": len(errors),
        "model_ids": sorted({str(item.get("model_id")) for item in values}),
        "latency_ms": {
            "p50": round(statistics.median(latencies), 3),
            "p95": round(p95_latency, 3),
            "max": round(max(latencies), 3),
        },
        "tokens": {
            "prompt_p50": round(statistics.median(prompt_tokens), 3),
            "completion_p50": round(statistics.median(completion_tokens), 3),
            "total_p50": round(statistics.median(total_tokens), 3),
            "total_p95": p95_tokens,
            "total_max": max(total_tokens),
        },
        "retry_call_count": sum("retry" in path for path in source_files.values()),
        "service": service,
        "gpu": gpu,
    }
    write_json(output_path, report)
    return report


__all__ = ["VLLM_BUDGETS", "run_vllm_evidence_benchmark"]
