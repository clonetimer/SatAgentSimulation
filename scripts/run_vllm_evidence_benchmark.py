#!/usr/bin/env python3
"""
用途：聚合真实 vLLM 调用证据并在线核验模型服务和 GPU 状态。
参数：可指定 reports 根目录、OpenAI-compatible 服务地址和输出报告。
输出：写入延迟、Token、错误、重试、模型身份和冻结预算判定 JSON。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sat_sim.vllm_evidence_benchmark import run_vllm_evidence_benchmark


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports-root", type=Path, default=Path("reports"))
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/vllm_evidence_benchmark.json"),
    )
    args = parser.parse_args()
    report = run_vllm_evidence_benchmark(
        reports_root=args.reports_root,
        output_path=args.output,
        base_url=args.base_url,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
