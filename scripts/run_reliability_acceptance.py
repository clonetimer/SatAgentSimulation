#!/usr/bin/env python3
"""
用途：聚合并执行经验纵向、容量、安全、确定性和中断恢复正式验收。
参数：项目根、三个前置报告、vLLM 地址/模型和输出目录。
输出：生成可靠性总报告及固定快照、故障注入和确定性逐项证据。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sat_sim.reliability_acceptance import run_reliability_acceptance


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument(
        "--longitudinal-report",
        type=Path,
        default=Path("reports/experience_longitudinal_eval/report.json"),
    )
    parser.add_argument(
        "--capacity-report",
        type=Path,
        default=Path("reports/capacity_benchmark/capacity_report.json"),
    )
    parser.add_argument(
        "--controlled-experience-report",
        type=Path,
        default=Path("reports/controlled_experience_acceptance/report.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/reliability_acceptance"),
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--model", default="Qwen3.5-9B")
    args = parser.parse_args()
    report = run_reliability_acceptance(
        project_root=args.project_root,
        longitudinal_report=args.longitudinal_report,
        capacity_report=args.capacity_report,
        controlled_experience_report=args.controlled_experience_report,
        output_dir=args.output_dir,
        base_url=args.base_url,
        model=args.model,
    )
    print(json.dumps({"status": report["status"], "checks": report["checks"]}, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
