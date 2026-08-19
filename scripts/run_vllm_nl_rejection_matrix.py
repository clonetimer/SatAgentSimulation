#!/usr/bin/env python3
"""
用途：在 vLLM 在线时执行自然语言拒绝和安全边界矩阵。
参数：可配置模型服务、模型名、超时和输出目录。
输出：写入逐案例结构化拒绝证据和汇总报告，并以退出码表示 PASS/FAIL。
"""
from __future__ import annotations

import argparse
import json
import shutil
import urllib.request
from pathlib import Path

from sat_sim.nl_rejection_matrix import REJECTION_CASES, evaluate_rejection_result
from sat_sim.task_spec import write_json
from sat_sim.unified_agent import UnifiedAgentRequest, run_unified_agent


def _models(base_url: str) -> dict:
    with urllib.request.urlopen(f"{base_url.rstrip('/')}/models", timeout=15.0) as response:  # nosec B310
        return json.loads(response.read(2_097_152).decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--model")
    parser.add_argument("--output-dir", type=Path, default=Path("reports/vllm_nl_rejection_matrix"))
    parser.add_argument("--timeout-s", type=float, default=300.0)
    args = parser.parse_args()

    output = args.output_dir.resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    model_payload = _models(args.base_url)
    available_models = [
        str(item.get("id"))
        for item in model_payload.get("data", [])
        if isinstance(item, dict) and item.get("id")
    ]
    model = args.model or (available_models[0] if available_models else None)
    if not model:
        raise RuntimeError("vLLM returned no available model")

    cases: list[dict] = []
    for case in REJECTION_CASES:
        case_dir = output / "cases" / case.case_id
        result = run_unified_agent(UnifiedAgentRequest(
            input_kind="natural_language",
            request_text=case.request_text,
            output_dir=case_dir,
            backend="openai_compatible",
            local_backend="template",
            remote_backend="openai_compatible",
            model_name=model,
            model_base_url=args.base_url,
            model_timeout_s=args.timeout_s,
            model_temperature=0.0,
            model_structured_output="json_schema",
            max_repair_attempts=0,
            compile_if_valid=True,
        ))
        cases.append(evaluate_rejection_result(case, result, evidence_dir=case_dir))

    passed = sum(1 for item in cases if item["passed"])
    report = {
        "schema_version": "sat-sim.vllm-nl-rejection-matrix.v1",
        "base_url": args.base_url,
        "model": model,
        "provider_available": model in available_models,
        "case_count": len(cases),
        "passed_count": passed,
        "failed_count": len(cases) - passed,
        "false_accept_count": sum(1 for item in cases if not item["checks"]["rejected"]),
        "generated_script_count": sum(1 for item in cases if not item["checks"]["no_generated_script"]),
        "model_invocation_count": sum(1 for item in cases if not item["checks"]["pre_model_rejection"]),
        "policy": "Unsafe and unsupported requests must be rejected before model invocation.",
        "status": "PASS" if passed == len(cases) and model in available_models else "FAIL",
        "cases": cases,
    }
    write_json(output / "report.json", report)
    print(json.dumps({
        key: report[key]
        for key in (
            "status", "case_count", "passed_count", "failed_count",
            "false_accept_count", "generated_script_count", "model_invocation_count",
        )
    }, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
