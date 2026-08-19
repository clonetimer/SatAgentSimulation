#!/usr/bin/env python3
"""
用途：分批执行由31对象场景基线自动生成的真实vLLM自然语言端到端矩阵。
参数：vLLM地址、模型、语言变体、模式、批次范围、超时和输出目录。
输出：生成案例清单、逐案例TaskSpec/脚本/Run Bundle证据及批次报告。
"""
from __future__ import annotations

import argparse
import json
import shutil
import urllib.request
from dataclasses import asdict
from pathlib import Path

from sat_sim.lmstudio_acceptance import run_lmstudio_acceptance
from sat_sim.nl_e2e_matrix import MODES, generate_nl_e2e_cases


def _discover_model(base_url: str) -> str:
    with urllib.request.urlopen(f"{base_url.rstrip('/')}/models", timeout=15.0) as response:  # nosec B310
        payload = json.loads(response.read(2_097_152).decode("utf-8"))
    return str(payload["data"][0]["id"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--model")
    parser.add_argument("--output-dir", type=Path, default=Path("reports/vllm_nl_e2e_matrix"))
    parser.add_argument("--variant", choices=("zh", "en", "alias"), default="zh")
    parser.add_argument("--mode", choices=(*MODES, "all"), default="all")
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout-s", type=float, default=300.0)
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    args = parser.parse_args()

    modes = MODES if args.mode == "all" else (args.mode,)
    all_cases = generate_nl_e2e_cases(variants=(args.variant,), modes=modes)
    stop = None if args.limit <= 0 else args.start_index + args.limit
    cases = all_cases[args.start_index:stop]
    output = args.output_dir.resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    (output / "generated_cases.json").write_text(
        json.dumps([asdict(case) for case in all_cases], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    model = args.model or _discover_model(args.base_url)
    semantic = run_lmstudio_acceptance(
        base_url=args.base_url,
        model=model,
        api_key_env="SAT_SIM_LOCAL_VLLM_API_KEY",
        timeout_s=args.timeout_s,
        output_dir=output / "evidence",
        execution_kind="protocol_fixture",
        cases=cases,
        require_loopback=True,
        max_output_tokens=args.max_output_tokens,
        execute_generated=True,
        execution_timeout_s=args.timeout_s,
        max_repair_attempts=0,
    )
    passed = int(semantic.summary.get("taskspec_passed") or 0)
    report = {
        "schema_version": "sat-sim.vllm-nl-e2e-matrix.v1",
        "model": model,
        "variant": args.variant,
        "modes": list(modes),
        "matrix_case_count": len(all_cases),
        "batch_start_index": args.start_index,
        "batch_case_count": len(cases),
        "passed_count": passed,
        "failed_count": len(cases) - passed,
        "fallback_detected": semantic.fallback_detected,
        "status": "PASS" if cases and passed == len(cases) and not semantic.fallback_detected else "FAIL",
        "cases": semantic.taskspec_cases,
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: report[key] for key in (
        "status", "matrix_case_count", "batch_case_count", "passed_count",
        "failed_count", "fallback_detected",
    )}, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
